"""Local status dashboard rendered to a static HTML file from the database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..agents.coordinator import DecisionLog
from ..config import Settings
from ..data.store import connect
from ..experiments.registry import Registry
from ..news.store import NewsStore
from ..risk.engine import KillSwitch
from .html import page, table


def _audit(db: Path, limit: int = 30) -> list[dict]:
    with connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, mode TEXT, "
                    "idempotency_key TEXT, decision TEXT, intent TEXT, risk TEXT, order_result TEXT)")
        con.row_factory = __import__("sqlite3").Row
        rows = [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
    out = []
    for r in rows:
        risk = json.loads(r["risk"]) if r["risk"] else {}
        res = json.loads(r["order_result"]) if r["order_result"] else {}
        out.append({"at": r["at"][:19], "mode": r["mode"], "key": r["idempotency_key"],
                    "risk": "approved" if risk.get("approved") else "; ".join(risk.get("reasons", [])) or "-",
                    "result": res.get("status", "-"), "message": res.get("message", "")})
    return out


def build_dashboard(s: Settings, out: Path | None = None) -> Path:
    db = s.paths.db_path
    ks = KillSwitch(s.paths.kill_switch_file)
    ns = NewsStore(db)
    now = datetime.now(timezone.utc)
    mode = "DEMO" if s.execution.enabled else "HISTORICAL"
    status = [{"item": "kill switch", "value": f"ENGAGED: {ks.reason()}" if ks.engaged() else "clear"},
              {"item": "order submission", "value": "ENABLED (demo/paper only)" if s.execution.enabled else "disabled (dry run)"},
              {"item": "broker", "value": s.execution.broker},
              {"item": "market", "value": f"{s.market.instrument} {s.market.timeframe}"},
              {"item": "llm", "value": s.llm.provider}]
    risk_rows = [{"limit": k, "value": v} for k, v in s.risk.model_dump().items()]
    events = [{"time": e["event_time"][:16], "ccy": e["currency"], "impact": e["impact"], "title": e["title"],
               "forecast": e["forecast"], "previous": e["previous"], "actual": e["actual"]}
              for e in ns.events(now - timedelta(days=1), now + timedelta(days=7)) if e["impact"] in ("high", "medium")]
    news = [{"ingested": n["ingested_at"][:16], "source": n["source"], "reliability": n["reliability"],
             "title": n["title"], "ccy": n["currencies"]} for n in ns.recent_news(25)]
    decisions = [{"at": d["decided_at"][:19], "strategy": d["strategy"], "mode": d["mode"], "action": d["action"],
                  "reasons": "; ".join(json.loads(d["reasons"]))} for d in DecisionLog(db).recent(25)]
    exps = [{"id": e["id"], "at": e["created_at"][:16], "kind": e["kind"], "strategy": e["strategy"], "source": e["data_source"],
             "trials": e["n_trials"], "sharpe": (e["metrics"].get("oos") or e["metrics"]).get("sharpe", ""),
             "verdict": e["verdict"] or ""} for e in Registry(db).list(25)]
    positions = []
    with connect(db) as con:
        if con.execute("SELECT name FROM sqlite_master WHERE name='paper_positions'").fetchone():
            positions = [{"instrument": i, "units": u, "avg_price": a, "stop": st}
                         for i, u, a, st in con.execute("SELECT instrument, units, avg_price, stop FROM paper_positions WHERE units != 0")]
    body = (f"<h2>System status</h2>{table(status)}<h2>Data-feed health</h2>{table(ns.health())}"
            f"<h2>Upcoming / recent economic events</h2>{table(events)}<h2>Recent news</h2>{table(news)}"
            f"<h2>Decisions</h2>{table(decisions)}<h2>Order audit (candidate trades and risk verdicts)</h2>{table(_audit(db))}"
            f"<h2>Open paper/demo positions</h2>{table(positions)}<h2>Risk limits</h2>{table(risk_rows)}"
            f"<h2>Experiments</h2>{table(exps)}")
    out = out or s.paths.reports_dir / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page("TradeIntel dashboard", mode, body), encoding="utf-8")
    return out
