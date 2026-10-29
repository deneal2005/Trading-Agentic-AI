"""One decision cycle, and a loop that repeats it. Safe to restart: all
state lives in SQLite, a file lock prevents concurrent cycles, and order
keys are derived from (strategy, instrument, bar time) so a repeated cycle
on the same bar cannot submit twice."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .agents.coordinator import DecisionContext, DecisionLog, coordinate
from .backtest.metrics import regime_labels
from .config import Settings
from .data.store import BarStore, connect
from .data.validate import TIMEFRAME_DELTA
from .execution.broker import Broker, BrokerError
from .execution.paper import PaperBroker
from .execution.pipeline import ExecutionPipeline
from .experiments.registry import Registry
from .news.store import NewsStore
from .risk.engine import EventWindow, KillSwitch, OrderIntent, RiskEngine
from .strategies.library import build

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

log = logging.getLogger(__name__)


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
        raise RuntimeError("another cycle is running")
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


def strategy_promoted(s: Settings, name: str) -> bool:
    return any(e["strategy"] == name and e["verdict"] == "promotable" and e["data_source"] != "SYNTHETIC"
               for e in Registry(s.paths.db_path).list(500, kind="walkforward"))


def run_cycle(s: Settings, broker: Broker | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    m = s.market
    with single_instance(s.paths.db_path.parent / "cycle.lock"):
        bars = BarStore(s.paths.db_path).load(m.instrument, m.timeframe)
        if bars.empty:
            return {"status": "abstain", "reason": "no stored bars; run `tradeintel data ingest` first"}
        strat = build(s.live.strategy, **s.live.params)
        sig = strat.signals(bars)
        ns = NewsStore(s.paths.db_path)
        events = ns.events(now - timedelta(days=2), now + timedelta(days=2))
        for e in events:
            e["analysis"] = json.loads(e["analysis"]) if e.get("analysis") else None
        broker = broker or make_broker(s)
        try:
            pos = [p for p in broker.positions() if p.instrument == m.instrument]
        except BrokerError as e:
            return {"status": "abstain", "reason": f"broker unavailable: {e}"}
        cur = (1 if pos[0].units > 0 else -1) if pos else 0
        max_age = TIMEFRAME_DELTA[m.timeframe] * (s.data.max_staleness_bars + 1) + (timedelta(days=2) if m.timeframe == "1d" else timedelta(0))
        ctx = DecisionContext(m.instrument, bars, sig, strat.name, now, max_age, cur, events,
                              regime=str(regime_labels(bars).iloc[-1]))
        d = coordinate(ctx, "multi")
        DecisionLog(s.paths.db_path).record(ctx, d)
        out = {"decision": d.action, "reasons": d.reasons}
        if d.action in ("hold", "abstain"):
            return out

        promoted = strategy_promoted(s, strat.name)
        enabled = s.execution.enabled and (promoted or not s.live.require_promotion)
        if s.execution.enabled and not enabled:
            out["note"] = f"{strat.name} has no promotable verdict on real data; dry run only"

        state = StateStore(s.paths.db_path)
        acct = broker.account()
        today = now.date().isoformat()
        if state.get("day") != today:
            state.set("day", today)
            state.set("day_start_equity", acct.equity)
        hwm = max(state.get("hwm", acct.equity), acct.equity)
        state.set("hwm", hwm)

        kill = KillSwitch(s.paths.kill_switch_file)
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
        out["execution"] = pipe.execute(intent, {"action": d.action, "reasons": d.reasons},
                                        state.get("day_start_equity", acct.equity), hwm,
                                        state.get("consecutive_losses", 0), m.pip_size, windows)
        return out


def run_service(s: Settings, interval_s: int = 900, max_cycles: int | None = None) -> None:
    n = 0
    while max_cycles is None or n < max_cycles:
        try:
            log.info("cycle", extra={"data": run_cycle(s)})
        except Exception:
            log.exception("cycle failed")
        n += 1
        time.sleep(interval_s)
