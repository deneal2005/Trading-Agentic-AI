"""One decision cycle, and a loop that repeats it. Safe to restart: all
state lives in SQLite, a file lock prevents concurrent cycles, and order
keys are derived from (strategy, instrument, bar time) so a repeated cycle
on the same bar cannot submit twice."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .agents.coordinator import DecisionContext, DecisionLog, coordinate
from .backtest.metrics import regime_labels
from .config import Settings
from .data.store import BarStore, connect
from .data.validate import TIMEFRAME_DELTA, drop_unfinished
from .execution.broker import Broker, BrokerError
from .execution.paper import PaperBroker
from .execution.pipeline import ExecutionPipeline
from .experiments.registry import Registry
from .jobs import JobStore, run_job
from .news.store import NewsStore
from .risk.engine import EventWindow, KillSwitch, OrderIntent, RiskEngine
from .strategies.library import build

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

log = logging.getLogger(__name__)


class CycleBusy(RuntimeError):
    """Another check holds the cycle lock."""


@contextmanager
def single_instance(lock_path):
    """Non-blocking exclusive lock so two cycles never run at once (POSIX and Windows)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        raise CycleBusy("another cycle is running")
    try:
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_UN)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


class StateStore:
    def __init__(self, db):
        self.db = db
        with connect(db) as con:
            con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")

    def get(self, k, default=None):
        with connect(self.db) as con:
            r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, k, v):
        with connect(self.db) as con:
            con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))


def make_broker(s: Settings, name: str | None = None) -> Broker:
    name = name or s.execution.broker
    if name == "oanda":
        from .execution.oanda import OandaPracticeBroker
        return OandaPracticeBroker()
    if name == "alpaca":
        from .execution.alpaca import AlpacaPaperBroker
        return AlpacaPaperBroker()
    return PaperBroker(s.paths.db_path, s.backtest.initial_equity, s.market.account_currency)


def max_bar_age(s: Settings) -> timedelta:
    """Age beyond which the latest bar counts as stale (weekends allowed for daily bars)."""
    tf = s.market.timeframe
    return TIMEFRAME_DELTA[tf] * (s.data.max_staleness_bars + 1) + (timedelta(days=2) if tf == "1d" else timedelta(0))


def strategy_promoted(s: Settings, name: str, params: dict | None = None) -> bool:
    """Approved to trade only for this exact market, timeframe and settings: a walk-forward on real data that traded
    these settings in every fold passed the gate, AND the single-use holdout with these settings passed."""
    try:
        live = build(name, **(params if params is not None else s.live.params if name == s.live.strategy else {})).params
    except (KeyError, ValueError):
        return False

    def same(p) -> bool:
        try:
            return build(name, **(p or {})).params == live
        except (KeyError, ValueError):
            return False

    m = s.market
    mine = [e for e in Registry(s.paths.db_path).list(100_000)
            if e["strategy"] == name and e["instrument"] == m.instrument and e["timeframe"] == m.timeframe
            and e["data_source"] not in ("SYNTHETIC", "unknown") and same(e["params"])]
    walk_forward_ok = any(e["kind"] == "walkforward" and e["verdict"] == "promotable"
                          and (folds := e["metrics"].get("folds")) and all(same(f["params"]) for f in folds) for e in mine)
    holdout_ok = any(e["kind"] == "holdout" and e["verdict"] == "passed" for e in mine)
    return walk_forward_ok and holdout_ok


def calendar_fresh(ns: NewsStore, now: datetime) -> bool:
    """True once this week's economic calendar has been fetched (the Forex Factory export covers Sunday to Saturday)."""
    week_start = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    with connect(ns.db_path) as con:  # any successful fetch this week; a later failed retry doesn't void it
        last_ok = con.execute("SELECT MAX(at) FROM source_health WHERE source='forexfactory' AND ok=1").fetchone()[0]
    return bool(last_ok) and last_ok >= week_start.isoformat()


def ensure_calendar(s: Settings, now: datetime | None = None) -> bool:
    """Refresh news and the calendar when this week's calendar is missing. Network failures are logged, not raised."""
    ns = NewsStore(s.paths.db_path)
    now = now or datetime.now(timezone.utc)
    if not calendar_fresh(ns, now):
        try:
            from .cli import news_ingest
            news_ingest(s)
        except Exception:
            log.exception("calendar refresh failed")
    return calendar_fresh(ns, now)


def account_guard(s: Settings, state: StateStore, kill: KillSwitch, equity: float, now: datetime) -> dict:
    """Runs on every cycle, order or not: records day-start equity and the high-water mark, and engages the kill
    switch on a daily-loss or drawdown breach. It doesn't close positions; that stays a human decision."""
    today = now.date().isoformat()
    if state.get("day") != today:
        state.set("day", today)
        state.set("day_start_equity", equity)
    hwm = max(state.get("hwm", equity), equity)
    state.set("hwm", hwm)
    day0 = state.get("day_start_equity", equity)
    daily, dd, breaches = loss_levels(s, day0, hwm, equity)
    for why in breaches:
        kill.engage(f"{why}, detected by the cycle check")
    return {"day_start_equity": day0, "hwm": hwm, "daily_loss": daily, "drawdown": dd}


def loss_levels(s: Settings, day0: float, hwm: float, equity: float) -> tuple[float, float, list[str]]:
    """Daily loss and drawdown, and which limits they breach. Shared by the cycle check and the stop release."""
    daily = (day0 - equity) / day0 if day0 else 0.0
    dd = (hwm - equity) / hwm if hwm else 0.0
    breaches = ([f"max daily loss reached ({daily:.2%})"] if daily >= s.risk.max_daily_loss else []) + \
               ([f"max drawdown reached ({dd:.2%})"] if dd >= s.risk.max_drawdown else [])
    return daily, dd, breaches


def release_kill_switch(s: Settings, who: str, now: datetime | None = None) -> str | None:
    """Turn the emergency stop off (app and CLI both come here) and audit it. Refused while a loss limit is still
    breached, because the next check would turn it straight back on. Returns the refusal, or None when released."""
    now = now or datetime.now(timezone.utc)
    state, kill = StateStore(s.paths.db_path), KillSwitch(s.paths.kill_switch_file)
    try:
        equity = make_broker(s).account().equity
    except BrokerError:
        equity = None  # can't check; nothing can trade without the broker anyway, and the next check re-evaluates
    if equity is not None:
        day0 = state.get("day_start_equity", equity) if state.get("day") == now.date().isoformat() else equity
        _, _, breaches = loss_levels(s, day0, max(state.get("hwm", equity), equity), equity)
        if breaches:
            return f"Not released: {' and '.join(breaches)} is still over its limit, so the next check would turn it back on."
    was = {"engaged": kill.engaged(), "reason": kill.reason() if kill.engaged() else None}
    kill.reset()
    state.set("consecutive_losses", 0)
    ExecutionPipeline(s.paths.db_path, None, None, False).audit(
        "manual", f"kill-reset|{now.isoformat()}", {"action": f"emergency stop turned off from {who}", "was": was},
        None, None, {"status": "cleared"})
    return None


def update_loss_streak(state: StateStore, instrument: str, pos, mark: float | None) -> None:
    """Counts losing round trips in a row for the consecutive-loss limit.
    shortcut: a trade is judged by the market price when the cycle notices it closed, not by its actual fill;
    upgrade once fills are reconciled (audit F-07)."""
    prev = state.get("open_trade")
    direction = (1 if pos.units > 0 else -1) if pos else 0
    if prev and prev["direction"] != direction:  # the tracked trade closed (stop, exit or reversal)
        if mark is None:
            return  # no price now; judge it on a later cycle
        loss = (mark - prev["entry"]) * prev["direction"] < 0
        state.set("consecutive_losses", state.get("consecutive_losses", 0) + 1 if loss else 0)
        state.set("open_trade", None)
        prev = None
    if pos and not prev:
        state.set("open_trade", {"instrument": instrument, "direction": direction, "entry": pos.avg_price})


CHECK_STEPS = ["Refreshing the economic calendar", "Loading price data", "Working out the strategy signal",
               "Checking the paper account", "AI agents reviewing", "Safety check and order"]


def check(s: Settings, progress=lambda _: None) -> dict:
    """One full check as a job body: refresh the calendar, then run a decision cycle."""
    progress(CHECK_STEPS[0])
    ensure_calendar(s)
    try:
        return {"ok": True, "result": run_cycle(s, progress=progress)}
    except CycleBusy:  # the app and the scheduler started at the same moment: one check already covers this
        return {"ok": True, "result": {"status": "skipped", "reason": "another check was already running"}}
    except RuntimeError as e:  # e.g. the broker refused or couldn't be reached
        return {"ok": False, "error": str(e)}


def run_cycle(s: Settings, broker: Broker | None = None, now: datetime | None = None, progress=lambda _: None) -> dict:
    now = now or datetime.now(timezone.utc)
    m = s.market
    with single_instance(s.paths.db_path.parent / "cycle.lock"):
        progress("Loading price data")
        bars = BarStore(s.paths.db_path).load(m.instrument, m.timeframe)
        if not bars.empty:  # a stored candle whose period isn't over yet must not drive a decision
            source = bars.attrs.get("source")
            bars, _ = drop_unfinished(bars, m.timeframe, m.asset_class, now)
            bars.attrs["source"] = source
        if bars.empty:
            return {"status": "abstain", "reason": "no stored bars; run `tradeintel data ingest` first"}
        progress("Working out the strategy signal")
        strat = build(s.live.strategy, **s.live.params)
        sig = strat.signals(bars)
        ns = NewsStore(s.paths.db_path)
        events = ns.events(now - timedelta(days=2), now + timedelta(days=2))
        for e in events:
            e["analysis"] = json.loads(e["analysis"]) if e.get("analysis") else None
        progress("Checking the paper account")
        broker = broker or make_broker(s)
        try:
            ExecutionPipeline(s.paths.db_path, broker, None, False).reconcile()  # follow up sent orders first
            pos = [p for p in broker.positions() if p.instrument == m.instrument]
            acct = broker.account()
        except BrokerError as e:
            return {"status": "abstain", "reason": f"broker unavailable: {e}"}
        cur = (1 if pos[0].units > 0 else -1) if pos else 0
        state = StateStore(s.paths.db_path)
        kill = KillSwitch(s.paths.kill_switch_file)
        guard = account_guard(s, state, kill, acct.equity, now)
        if pos or state.get("open_trade"):
            try:
                q = broker.quote(m.instrument)
                mark = (q.bid + q.ask) / 2
            except BrokerError:
                mark = None
            update_loss_streak(state, m.instrument, pos[0] if pos else None, mark)
        progress("AI agents reviewing")
        ctx = DecisionContext(m.instrument, bars, sig, strat.name, now, max_bar_age(s), cur, events,
                              regime=str(regime_labels(bars).iloc[-1]))
        d = coordinate(ctx, "multi")
        DecisionLog(s.paths.db_path).record(ctx, d)
        out = {"decision": d.action, "reasons": d.reasons, "account": guard}
        if kill.engaged():
            out["halted"] = kill.reason()
        if d.action in ("hold", "abstain"):
            return out

        progress("Safety check and order")
        promoted = strategy_promoted(s, strat.name)
        enabled = s.execution.enabled and (promoted or not s.live.require_promotion)
        if s.execution.enabled and not enabled:
            out["note"] = (f"{strat.name} isn't approved for {m.instrument} {m.timeframe} with these settings "
                           "(needs a passing fixed-settings walk-forward and holdout on real data); dry run only")
        hwm = guard["hwm"]

        pipe = ExecutionPipeline(s.paths.db_path, broker, RiskEngine(s.risk, kill), enabled)
        pipe.risk._seen_keys |= pipe.submitted_keys()

        if d.action == "exit" or (cur != 0):
            if cur != 0 and enabled:
                res = broker.close_all()
                pipe.audit(acct.environment, f"exit|{bars.index[-1]}", {"action": d.action}, None, None,
                           {"status": "closed", "n": len(res)})
                out["closed"] = len(res)
            if d.action == "exit":
                return out

        direction = 1 if d.action == "enter_long" else -1
        q = broker.quote(m.instrument)
        entry = q.ask if direction == 1 else q.bid
        stop_dist = float(sig["stop_dist"].iloc[-1])
        if not stop_dist > 0:
            out["status"] = "abstain: no valid stop distance"
            return out
        stop = entry - direction * stop_dist
        units = float(int(acct.equity * s.risk.max_risk_per_trade / stop_dist)) if acct.currency == m.quote_ccy else 0.0
        intent = OrderIntent(m.instrument, direction, units, entry, stop, None, strat.name,
                             f"{strat.name}|{m.instrument}|{bars.index[-1].isoformat()}|{direction}", now)
        windows = [EventWindow(datetime.fromisoformat(e["event_time"]), (e["currency"],), e["title"])
                   for e in events if e["impact"] == "high"]
        if not calendar_fresh(ns, now):
            windows = None  # unknown event risk: the risk engine refuses new entries
            out["calendar"] = "this week's economic calendar is missing; new entries blocked"
        out["execution"] = pipe.execute(intent, {"action": d.action, "reasons": d.reasons},
                                        guard["day_start_equity"], hwm,
                                        state.get("consecutive_losses", 0), m.pip_size, windows)
        return out


def run_service(s: Settings, interval_s: int = 900, max_cycles: int | None = None) -> None:
    """Scheduled checks. Each one is recorded as a job, and a heartbeat tells the app when the next is due."""
    jobs, state = JobStore(s.paths.db_path), StateStore(s.paths.db_path)
    n = 0
    while max_cycles is None or n < max_cycles:
        out = run_job(jobs, "check", "Scheduled check", CHECK_STEPS, lambda p: check(s, p), source="service")
        log.info("cycle", extra={"data": out})
        now = datetime.now(timezone.utc)
        state.set("service_heartbeat", {"pid": os.getpid(), "interval_s": interval_s, "at": now.isoformat(),
                                        "next_at": (now + timedelta(seconds=interval_s)).isoformat()})
        n += 1
        time.sleep(interval_s)
