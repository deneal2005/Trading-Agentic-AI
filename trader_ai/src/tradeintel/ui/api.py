"""Read models and actions behind the local Trading OS UI.

Every value comes from the database, the config or the configured broker.
Nothing is invented: a missing source is returned as unavailable with the
reason, and the UI shows it that way. Actions call the same code as the CLI,
so the risk engine, kill switch and promotion gate apply exactly as there.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..agents import coordinator as agents
from ..agents.coordinator import DecisionLog
from ..backtest.metrics import regime_labels
from ..config import Settings
from ..data.store import BarStore, connect
from ..experiments.registry import Registry, code_version
from ..news.store import NewsStore
from ..risk.engine import KillSwitch, currency_legs
from ..strategies.indicators import sma
from ..strategies.library import REGISTRY, build

SECRET_ENV = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "OANDA_API_TOKEN", "OANDA_ACCOUNT_ID", "ANTHROPIC_API_KEY")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _j(x):
    return json.loads(x) if x else None


def _num(x):
    """JSON-safe float (NaN/inf -> None)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


# ---------------------------------------------------------------- broker

def broker_snapshot(s: Settings) -> dict:
    from ..service import make_broker

    out: dict = {"broker": s.execution.broker, "fetched_at": _now().isoformat(), "available": False}
    try:
        b = make_broker(s)
        acct = b.account()
        out.update(available=True, environment=acct.environment, account="..." + acct.account_id[-4:],
                   currency=acct.currency, cash=acct.balance, equity=acct.equity,
                   positions=[asdict(p) for p in b.positions()])
    except Exception as e:  # UI boundary: report, never crash
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:
        q = b.quote(s.market.instrument)
        out["quote"] = {"instrument": q.instrument, "bid": q.bid, "ask": q.ask, "spread": round(q.ask - q.bid, 6),
                        "spread_pips": round((q.ask - q.bid) / s.market.pip_size, 2), "time": q.time.isoformat()}
    except Exception as e:
        out["quote_error"] = f"{e}" + (" (the internal paper broker has no live prices)" if s.execution.broker == "paper" else "")
    for name in ("clock", "open_orders"):
        if hasattr(b, name):
            try:
                out[name] = getattr(b, name)()
            except Exception as e:
                out[f"{name}_error"] = str(e)
    return out


class BrokerCache:
    """Broker reads are network calls (an Alpaca quote takes ~2 s), so one snapshot is reused briefly."""

    def __init__(self, s: Settings, ttl: float = 15.0, fetch=broker_snapshot):
        self.s, self.ttl, self.fetch = s, ttl, fetch
        self._lock = threading.Lock()
        self._snap: dict | None = None
        self._at = 0.0

    def get(self, force: bool = False) -> dict:
        with self._lock:
            if force or self._snap is None or time.monotonic() - self._at > self.ttl:
                self._snap = self.fetch(self.s)
                self._at = time.monotonic()
            return self._snap


# ---------------------------------------------------------------- helpers

def _bars(s: Settings, instrument: str | None = None, timeframe: str | None = None) -> tuple[pd.DataFrame, str | None]:
    try:
        return BarStore(s.paths.db_path).load(instrument or s.market.instrument, timeframe or s.market.timeframe), None
    except ValueError as e:  # e.g. two sources stored for the same series
        return pd.DataFrame(), str(e)


def stored_series(s: Settings) -> list[dict]:
    with connect(s.paths.db_path) as con:
        rows = con.execute("SELECT instrument, timeframe, source, COUNT(*), MIN(ts), MAX(ts) FROM bars "
                           "GROUP BY instrument, timeframe, source ORDER BY instrument").fetchall() \
            if con.execute("SELECT name FROM sqlite_master WHERE name='bars'").fetchone() else []
    return [{"instrument": i, "timeframe": tf, "source": src, "rows": n, "first": a, "last": b} for i, tf, src, n, a, b in rows]


def _kv(s: Settings, key: str):
    from ..service import StateStore
    return StateStore(s.paths.db_path).get(key)


def kill_switch(s: Settings) -> dict:
    ks = KillSwitch(s.paths.kill_switch_file)
    if not ks.engaged():
        return {"engaged": False}
    try:
        info = json.loads(s.paths.kill_switch_file.read_text(encoding="utf-8"))
    except Exception:
        info = {}
    return {"engaged": True, "reason": info.get("reason", ks.reason()), "at": info.get("at")}


def audit_rows(s: Settings, limit: int = 100) -> list[dict]:
    with connect(s.paths.db_path) as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE name='audit'").fetchone():
            return []
        con.row_factory = __import__("sqlite3").Row
        rows = [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        for k in ("decision", "intent", "risk", "order_result"):
            r[k] = _j(r[k])
    return rows


def decision_rows(s: Settings, limit: int = 50) -> list[dict]:
    rows = DecisionLog(s.paths.db_path).recent(limit)
    for r in rows:
        for k in ("reasons", "reports", "inputs"):
            r[k] = _j(r[k])
    return rows


def _position_notional(p: dict, account_ccy: str) -> float | None:
    inst, units, px = p["instrument"], abs(p["units"]), p["avg_price"]
    if len(inst) == 6 and inst.isalpha():
        if inst[3:] == account_ccy:
            return units * px
        if inst[:3] == account_ccy:
            return units
        return None
    return units * px  # stock/ETF priced in the account currency


# ---------------------------------------------------------------- read models

def state(s: Settings, cache: BrokerCache) -> dict:
    from ..service import max_bar_age

    now = _now()
    bars, bars_err = _bars(s)
    last_bar = bars.index[-1] if len(bars) else None
    data = {"instrument": s.market.instrument, "timeframe": s.market.timeframe, "rows": len(bars),
            "source": bars.attrs.get("source") if len(bars) else None, "error": bars_err,
            "last_bar": last_bar.isoformat() if last_bar is not None else None,
            "stale": last_bar is None or now - last_bar.to_pydatetime() > max_bar_age(s),
            "last_close": _num(bars["close"].iloc[-1]) if len(bars) else None,
            "prev_close": _num(bars["close"].iloc[-2]) if len(bars) > 1 else None}
    br = cache.get()
    last = decision_rows(s, 1)
    ks = kill_switch(s)
    return {"server_time": now.isoformat(), "code_version": code_version(),
            "mode": {"execution_enabled": s.execution.enabled, "broker": s.execution.broker,
                     "require_promotion": s.live.require_promotion, "live_trading": False,
                     "label": "Paper orders enabled (promoted strategies only)" if s.execution.enabled else "Observation only"},
            "market": {"instrument": s.market.instrument, "asset_class": s.market.asset_class, "timeframe": s.market.timeframe},
            "strategy": {"name": s.live.strategy, "params": s.live.params},
            "kill_switch": ks, "data": data, "broker": br, "llm": s.llm.provider,
            "account_state": {"day": _kv(s, "day"), "day_start_equity": _kv(s, "day_start_equity"), "hwm": _kv(s, "hwm")},
            "agent": {"state": "halted" if ks["engaged"] else "idle",
                      "last_decision": last[0] if last else None,
                      "note": "Decision cycles run only via `tradeintel run-once` or `tradeintel service`. "
                              "No scheduler heartbeat is recorded yet, so a running service can't be detected."},
            "alerts": alerts(s, br, data, ks)}


def alerts(s: Settings, br: dict, data: dict, ks: dict) -> list[dict]:
    out = []

    def add(level, key, title, detail="", at=None):
        out.append({"level": level, "key": key, "title": title, "detail": detail, "at": at})

    if ks["engaged"]:
        add("danger", f"kill:{ks.get('at')}", "Kill switch engaged", ks.get("reason", ""), ks.get("at"))
    if not br.get("available"):
        add("danger", "broker", f"Broker '{br.get('broker')}' unavailable", br.get("error", ""))
    elif br.get("quote_error"):
        add("warn", "quote", "No live quote", br["quote_error"])
    elif br.get("quote") and br["quote"]["spread_pips"] > s.risk.max_spread_pips:
        q = br["quote"]
        add("warn", f"spread:{q['time'][:16]}", f"Spread {q['spread']} is above the risk limit",
            f"{q['spread_pips']} > {s.risk.max_spread_pips} pips/cents: the risk engine would reject an order now")
    if data["error"]:
        add("danger", "bars", "Stored bars can't be loaded", data["error"])
    elif data["stale"]:
        add("warn", f"stale:{data['last_bar']}", "Market data is stale",
            f"last {data['instrument']} bar {data['last_bar'] or 'none'}; run Refresh data")
    for h in NewsStore(s.paths.db_path).health():
        if not h["ok"]:
            add("warn", f"feed:{h['source']}:{h['at']}", f"News feed '{h['source']}' failed", h["error"] or "", h["at"])
    day_ago = (_now() - timedelta(days=1)).isoformat()
    for r in audit_rows(s, 50):
        risk = r["risk"] or {}
        if r["at"] >= day_ago and risk and not risk.get("approved"):
            add("warn", f"reject:{r['id']}", "Order intent rejected by risk", "; ".join(risk.get("reasons", [])), r["at"])
    return out


def bars_view(s: Settings, instrument: str, timeframe: str, limit: int, cache: BrokerCache) -> dict:
    bars, err = _bars(s, instrument, timeframe)
    if err or bars.empty:
        return {"available": False, "error": err or f"no stored {instrument} {timeframe} bars; run Refresh data"}
    live = instrument == s.market.instrument
    strat = build(s.live.strategy, **s.live.params)
    sig = strat.signals(bars)
    sma50, sma200 = sma(bars["close"], 50), sma(bars["close"], 200)
    tail = bars.iloc[-limit:] if limit else bars
    t0 = tail.index[0]

    def ts(i):
        return int(i.timestamp())

    candles = [{"time": ts(i), "open": _num(r.open), "high": _num(r.high), "low": _num(r.low), "close": _num(r.close),
                "volume": _num(r.volume)} for i, r in tail.iterrows()]
    tgt = sig["target"]
    changes = tgt[tgt.ne(tgt.shift())].loc[t0:]
    markers = [{"time": ts(i), "kind": "signal", "target": int(v)} for i, v in changes.items()]
    decisions = []
    for d in decision_rows(s, 200):
        lb = (d["inputs"] or {}).get("last_bar")
        if d["instrument"] == instrument and lb and pd.Timestamp(lb) >= t0:
            decisions.append({"time": ts(pd.Timestamp(lb)), "action": d["action"], "id": d["id"], "decided_at": d["decided_at"]})
    lines = []
    if live:
        br = cache.get()
        for p in br.get("positions") or []:
            if p["instrument"] == instrument:
                lines.append({"price": p["avg_price"], "label": f"avg entry {p['units']:+g}"})
                if p.get("stop_price"):
                    lines.append({"price": p["stop_price"], "label": "stop"})
    regime = regime_labels(bars)
    return {"available": True, "instrument": instrument, "timeframe": timeframe, "source": bars.attrs.get("source"),
            "rows": len(bars), "candles": candles,
            "overlays": {"sma50": [{"time": ts(i), "value": _num(v)} for i, v in sma50.loc[t0:].dropna().items()],
                         "sma200": [{"time": ts(i), "value": _num(v)} for i, v in sma200.loc[t0:].dropna().items()]},
            "markers": markers, "decisions": decisions, "price_lines": lines,
            "strategy": {"name": strat.name, "describe": strat.describe(), "target": int(tgt.iloc[-1]),
                         "stop_dist": _num(sig["stop_dist"].iloc[-1]), "is_live_instrument": live,
                         "note": "Signal changes are what the strategy rule says on each closed bar. They are not trades."},
            "regime": str(regime.iloc[-1])}


def watchlist(s: Settings, cache: BrokerCache) -> list[dict]:
    out = []
    br = cache.get()
    for row in stored_series(s):
        bars, _ = _bars(s, row["instrument"], row["timeframe"])
        if len(bars) < 2:
            continue
        c, p = float(bars["close"].iloc[-1]), float(bars["close"].iloc[-2])
        q = br.get("quote") if row["instrument"] == s.market.instrument else None
        out.append({**row, "last_close": c, "change_pct": c / p - 1, "volume": _num(bars["volume"].iloc[-1]),
                    "quote": q, "is_live_instrument": row["instrument"] == s.market.instrument})
    return out


def agents_view(s: Settings) -> dict:
    rows = decision_rows(s, 200)
    stats: dict[str, dict] = {}
    for d in rows:
        for r in d["reports"] or []:
            st = stats.setdefault(r["agent"], {"runs": 0, "stances": {}, "last": None, "last_at": None})
            st["runs"] += 1
            st["stances"][r["stance"]] = st["stances"].get(r["stance"], 0) + 1
            if st["last"] is None:
                st["last"], st["last_at"] = r, d["decided_at"]
    actions: dict[str, int] = {}
    for d in rows:
        actions[d["action"]] = actions.get(d["action"], 0) + 1
    non_fx = not (len(s.market.instrument) == 6 and s.market.instrument.isalpha())

    def doc(fn):
        return (inspect.getdoc(fn) or "").split("\n\n")[0]

    nodes = [
        {"id": "market", "kind": "agent", "name": "Market analyst", "fn": "market_analyst",
         "purpose": "Reads the strategy signal on the latest closed bar; abstains if the data is missing or stale.",
         "reads": ["stored bars", f"{s.live.strategy} signal", "regime label"], "permissions": ["read-only"],
         "diagnostics": []},
        {"id": "news", "kind": "agent", "name": "News analyst", "fn": "news_analyst",
         "purpose": "Scores analysed economic events per currency and objects to entries before high-impact events.",
         "reads": ["economic calendar", "event analysis"], "permissions": ["read-only"],
         "diagnostics": [f"Matches events by currency pair letters, so for {s.market.instrument} it sees no events "
                         "(the risk engine's USD blackout still applies)."] if non_fx else []},
        {"id": "quant", "kind": "agent", "name": "Quant analyst", "fn": "quant_analyst",
         "purpose": "Turns the direction model's P(up) into a stance.",
         "reads": ["direction model probability"], "permissions": ["read-only"],
         "diagnostics": ["Not wired: the live cycle passes no model probability, so this agent always abstains."]},
        {"id": "adversarial", "kind": "agent", "name": "Adversarial reviewer", "fn": "adversarial_reviewer",
         "purpose": doc(agents.adversarial_reviewer), "reads": ["other agents' reports", "regime"], "permissions": ["read-only"],
         "diagnostics": []},
        {"id": "coordinator", "kind": "control", "name": "Coordinator", "fn": "coordinate",
         "purpose": "Runs each agent once and abstains on missing data or any objection to a new entry. "
                    "Produces a proposal only.", "reads": ["agent reports"], "permissions": ["propose"],
         "config": {"mode": "multi", "max_objections": 0}, "diagnostics": []},
        {"id": "risk", "kind": "control", "name": "Risk engine", "fn": "RiskEngine.evaluate",
         "purpose": "Deterministic limits. Vetoes any order and engages the kill switch on loss/drawdown breaches.",
         "reads": ["account", "quote", "positions", "events"], "permissions": ["veto", "engage kill switch"],
         "config": s.risk.model_dump(), "diagnostics": []},
        {"id": "execution", "kind": "control", "name": "Execution pipeline", "fn": "ExecutionPipeline.execute",
         "purpose": "Sends an approved order to the broker only if execution is enabled and the strategy is promoted. "
                    "Audits every attempt.", "reads": ["risk decision"],
         "permissions": ["submit paper/practice orders" if s.execution.enabled else "none (execution disabled)"],
         "config": {"enabled": s.execution.enabled, "broker": s.execution.broker, "require_promotion": s.live.require_promotion},
         "diagnostics": []},
    ]
    for n in nodes:
        n["stats"] = stats.get(n["id"])
    if rows:  # the coordinator's output is the decision itself
        nodes[4]["stats"] = {"runs": len(rows), "stances": actions, "last": {"action": rows[0]["action"], "reasons": rows[0]["reasons"]},
                             "last_at": rows[0]["decided_at"]}
    edges = [["market", "coordinator"], ["news", "adversarial"], ["quant", "adversarial"], ["market", "adversarial"],
             ["adversarial", "coordinator"], ["coordinator", "risk"], ["risk", "execution"]]
    from ..models.forecast import ModelRegistry
    from ..kb.store import KnowledgeBase
    try:
        mr = ModelRegistry(s.paths.models_dir)
        models = mr.versions("direction_logit") + mr.versions("direction_mlp")
    except Exception as e:
        models = [{"error": str(e)}]
    try:
        kb = KnowledgeBase(s.paths.db_path).stats()
    except Exception as e:
        kb = {"error": str(e)}
    return {"nodes": nodes, "edges": edges, "decision_actions": actions, "decisions_counted": len(rows),
            "version": code_version(), "models": models, "knowledge_base": kb,
            "not_recorded": ["per-agent execution time", "tool-call traces", "LLM reasoning (agents are rule-based)"]}


def decisions_view(s: Settings, limit: int) -> list[dict]:
    audits = audit_rows(s, 500)
    out = []
    for d in decision_rows(s, limit):
        lb = (d["inputs"] or {}).get("last_bar")
        prefix = f"{d['strategy']}|{d['instrument']}|{pd.Timestamp(lb).isoformat()}|" if lb else None
        d["audit"] = [a for a in audits if prefix and (a["idempotency_key"] or "").startswith(prefix)]
        out.append(d)
    return out


def risk_view(s: Settings, cache: BrokerCache) -> dict:
    br = cache.get()
    eq = br.get("equity")
    day_start, hwm = _kv(s, "day_start_equity"), _kv(s, "hwm")
    positions = br.get("positions") or []
    notionals = [_position_notional(p, br.get("currency", s.market.account_currency)) for p in positions]
    lev = (sum(n for n in notionals if n is not None) / eq) if eq and None not in notionals else None
    r = s.risk
    util = [
        {"limit": "Daily loss", "max": r.max_daily_loss, "unit": "pct",
         "value": (day_start - eq) / day_start if eq and day_start else None,
         "note": "" if day_start else "day-start equity is recorded by the first decision cycle of the day"},
        {"limit": "Drawdown from high-water mark", "max": r.max_drawdown, "unit": "pct",
         "value": (max(hwm, eq) - eq) / max(hwm, eq) if eq and hwm else None,
         "note": "" if hwm else "high-water mark is recorded by decision cycles"},
        {"limit": "Leverage", "max": r.max_leverage, "unit": "x", "value": lev,
         "note": "" if lev is not None else "unavailable (no broker data, or cross-currency position)"},
        {"limit": "Open positions per instrument", "max": r.max_positions_per_instrument, "unit": "count",
         "value": max([sum(1 for p in positions if p["instrument"] == i) for i in {p["instrument"] for p in positions}] or [0])
         if br.get("available") else None, "note": ""},
        {"limit": "Aggregate open risk", "max": r.max_open_risk, "unit": "pct",
         "value": 0.0 if br.get("available") and not positions else None,
         "note": "" if not positions else "the broker doesn't report stops on positions, so the engine treats open risk as unbounded"},
        {"limit": "Consecutive losses", "max": r.max_consecutive_losses, "unit": "count", "value": _kv(s, "consecutive_losses"),
         "note": "counter isn't wired to closed trades yet (see PROJECT_PLAN)"},
    ]
    q = br.get("quote")
    if q:
        util.append({"limit": "Spread (current quote)", "max": r.max_spread_pips, "unit": "pips", "value": q["spread_pips"],
                     "note": f"{s.market.instrument}: 1 pip = {s.market.pip_size}"})
    now = _now()
    legs = set(currency_legs(s.market.instrument, 1))
    affected = legs if len(legs) > 1 else legs | {s.market.account_currency}
    window = timedelta(minutes=r.event_blackout_minutes)
    blackout = [e for e in NewsStore(s.paths.db_path).events(now - window, now + timedelta(days=2), impact="high")
                if e["currency"] in affected]
    rejections = [a for a in audit_rows(s, 200) if a["risk"] and not a["risk"].get("approved")][:50]
    return {"limits": r.model_dump(), "utilisation": util, "kill_switch": kill_switch(s), "blackout_events": blackout,
            "blackout_currencies": sorted(affected), "rejections": rejections,
            "editing": "Risk limits are read-only here by design (RISK_POLICY: nothing at runtime writes risk configuration). "
                       "Change them in config/local.yaml and restart."}


def research_view(s: Settings) -> dict:
    exps = Registry(s.paths.db_path).list(200)
    from ..service import strategy_promoted
    reg = Registry(s.paths.db_path)
    latest_seen = set()
    for e in exps:  # the HTML report is overwritten per strategy, so only the newest run links to it
        path = s.paths.reports_dir / f"research_{e['strategy']}.html"
        if e["kind"] == "walkforward" and e["strategy"] not in latest_seen and path.exists():
            e["report"] = f"/reports/research_{e['strategy']}.html"
        latest_seen.add(e["strategy"])
        e.pop("config_hash", None)
    strategies = []
    for name, cls in REGISTRY.items():
        st = cls()
        strategies.append({"name": name, "family": cls.family, "doc": inspect.getdoc(cls) or "",
                           "defaults": st.params, "grid_size": len(st.grid()), "requires_stop": cls.requires_stop,
                           "baseline": name in ("no_trade", "buy_and_hold"), "is_live": name == s.live.strategy,
                           "total_trials": reg.total_trials(name), "promoted": strategy_promoted(s, name),
                           "verdicts": [{"id": e["id"], "instrument": e["instrument"], "source": e["data_source"],
                                         "verdict": e["verdict"]} for e in exps if e["strategy"] == name and e["kind"] == "walkforward"][:5]})
    return {"strategies": strategies, "experiments": exps,
            "costs": s.costs.model_dump(), "promotion": s.promotion.model_dump(),
            "paper_vs_backtest": None}


def news_view(s: Settings) -> dict:
    ns = NewsStore(s.paths.db_path)
    now = _now()
    return {"events": ns.events(now - timedelta(days=2), now + timedelta(days=7)),
            "news": [{k: n[k] for k in ("id", "source", "reliability", "title", "url", "published_at", "ingested_at", "currencies")}
                     for n in ns.recent_news(60)],
            "health": ns.health()}


def logs_view(s: Settings, limit: int, level: str | None) -> dict:
    path = s.paths.data_dir.parent / "logs" / "tradeintel.log"
    if not path.exists():
        return {"available": False, "path": str(path), "lines": []}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
    out = []
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except ValueError:
            rec = {"msg": ln, "level": "RAW"}
        if level and rec.get("level") != level:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return {"available": True, "path": str(path), "lines": out}


def config_view(s: Settings) -> dict:
    return {"settings": s.model_dump(mode="json"),
            "env": {k: bool(os.environ.get(k)) for k in SECRET_ENV},
            "series": stored_series(s)}


def brain_view(s: Settings, history: int = 120) -> dict:
    """The trained neural network's real weights, plus its forward pass on the latest stored bars."""
    import pickle

    import numpy as np

    from ..models.forecast import ModelRegistry, make_features

    versions = ModelRegistry(s.paths.models_dir).versions("direction_mlp")
    if not versions:
        return {"available": False, "error": "No neural network trained yet. Run `tradeintel model-eval --model mlp`."}
    meta = versions[-1]
    with open(s.paths.models_dir / "direction_mlp" / meta["version"] / "model.pkl", "rb") as f:
        pipe = pickle.load(f)  # our own artifact from ModelRegistry.save
    scaler, mlp = pipe.steps[0][1], pipe.steps[-1][1]
    feats = meta["features"]
    bars, err = _bars(s)
    if err or bars.empty:
        return {"available": False, "error": err or "no stored bars"}
    X = make_features(bars)[feats].dropna().iloc[-history:]
    acts = [scaler.transform(X)]
    for i, (W, b) in enumerate(zip(mlp.coefs_, mlp.intercepts_)):
        z = acts[-1] @ W + b
        acts.append(np.tanh(z) if i < len(mlp.coefs_) - 1 else 1 / (1 + np.exp(-z)))
    # self-check: this forward pass must equal scikit-learn's own prediction
    diff = float(np.max(np.abs(acts[-1][:, 0] - pipe.predict_proba(X)[:, 1])))
    if diff > 1e-6:
        raise ValueError(f"forward pass mismatch {diff}")
    c = bars["close"]
    actual = (c.shift(-1) > c).where(c.shift(-1).notna()).reindex(X.index)
    hist = [{"time": t.isoformat(), "acts": [a[i].round(4).tolist() for a in acts], "p_up": round(float(acts[-1][i, 0]), 4),
             "actual_up": None if pd.isna(actual.iloc[i]) else bool(actual.iloc[i])} for i, t in enumerate(X.index)]
    return {"available": True, "version": meta["version"], "verdict": meta.get("verdict"), "reasons": meta.get("reasons", []),
            "model": meta.get("model"), "baseline": (meta.get("baselines") or {}).get("base_rate"),
            "n_predictions": meta.get("n_predictions"), "n_folds": meta.get("n_folds"), "data_source": meta.get("data_source"),
            "instrument": s.market.instrument, "features": feats, "raw_latest": {k: _num(v) for k, v in X.iloc[-1].items()},
            "layers": [len(feats), *[w.shape[1] for w in mlp.coefs_]],
            "weights": [W.round(4).tolist() for W in mlp.coefs_],  # weights[l][from][to]
            "biases": [b.round(4).tolist() for b in mlp.intercepts_],
            "n_params": int(sum(W.size + b.size for W, b in zip(mlp.coefs_, mlp.intercepts_))),
            "activation": mlp.activation, "history": hist, "forward_pass_check": diff,
            "used_by_agents": False,
            "note": "Weights are the final walk-forward fold's trained network. Activations are its real forward pass on each "
                    "stored daily bar; they change when a new bar is ingested, not tick by tick."}


# ---------------------------------------------------------------- actions

_heavy = threading.Lock()  # one compute-heavy action at a time


def act_kill(s: Settings, reason: str) -> dict:
    reason = (reason or "").strip()[:200] or "engaged from UI"
    KillSwitch(s.paths.kill_switch_file).engage(f"ui: {reason}")
    return {"ok": True, "kill_switch": kill_switch(s),
            "note": "New orders are blocked. Open positions and resting broker orders are NOT closed or cancelled."}


def act_run_once(s: Settings) -> dict:
    from ..service import run_cycle
    try:
        return {"ok": True, "result": run_cycle(s)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


def act_ingest(s: Settings) -> dict:
    from ..cli import ingest
    from ..data.providers import DataUnavailable
    try:
        return {"ok": True, "result": ingest(s)}
    except (DataUnavailable, ValueError) as e:
        return {"ok": False, "error": str(e)}


def act_news_ingest(s: Settings) -> dict:
    from ..cli import news_ingest
    return {"ok": True, "result": news_ingest(s)}


def act_backtest(s: Settings, strategy: str, params: dict | None) -> dict:
    from ..backtest.engine import run_backtest
    from ..backtest.metrics import compute_metrics
    from ..backtest.research import engine_params

    if strategy not in REGISTRY:
        return {"ok": False, "error": f"unknown strategy {strategy}"}
    bars, err = _bars(s)
    if err or bars.empty:
        return {"ok": False, "error": err or "no stored bars"}
    try:
        strat = build(strategy, **(params or {}))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with _heavy:
        ep = engine_params(s)
        ep.require_stop = strat.requires_stop
        res = run_backtest(bars, strat.signals(bars), s.costs, ep)
        bh = run_backtest(bars, build("buy_and_hold").signals(bars), s.costs, engine_params(s))
    step = max(1, len(res.equity) // 1500)
    return {"ok": True, "kind": "in-sample backtest", "strategy": strat.describe(), "source": bars.attrs.get("source"),
            "warning": "Single full-sample backtest: in-sample and for inspection only. It is NOT evidence; use Research.",
            "metrics": {k: (_num(v) if isinstance(v, float) else v) for k, v in compute_metrics(res, s.market.timeframe).items()},
            "equity": [{"time": int(i.timestamp()), "value": _num(v)} for i, v in res.equity.iloc[::step].items()],
            "benchmark": [{"time": int(i.timestamp()), "value": _num(v)} for i, v in bh.equity.iloc[::step].items()]}


def act_research(s: Settings, strategy: str) -> dict:
    from ..backtest.research import research
    from ..reporting.html import research_report

    if strategy not in REGISTRY or strategy in ("no_trade", "buy_and_hold"):
        return {"ok": False, "error": f"not a researchable strategy: {strategy}"}
    bars, err = _bars(s)
    if err or bars.empty:
        return {"ok": False, "error": err or "no stored bars"}
    if not _heavy.acquire(blocking=False):
        return {"ok": False, "error": "another backtest or research run is in progress"}
    try:
        rep = research(s, bars, strategy, registry=Registry(s.paths.db_path))
        research_report(rep, s.paths.reports_dir / f"research_{strategy}.html")
    finally:
        _heavy.release()
    return {"ok": True, "experiment_id": rep.get("experiment_id"), "gate": rep["gate"],
            "oos": {k: _num(rep["oos"][k]) for k in ("sharpe", "net_return", "max_drawdown", "n_trades", "profit_factor")},
            "report": f"/reports/research_{strategy}.html"}
