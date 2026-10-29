"""Order pipeline: decision -> risk check -> broker -> audit record.

Order submission requires ALL of: execution.enabled true in config, a
broker that reports a non-live environment, a clear kill switch, and a risk
approval. Every attempt, approved or not, is written to the audit table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..data.store import connect
from ..risk.engine import AccountState, EventWindow, MarketState, OrderIntent, Position, RiskEngine
from .broker import Broker, BrokerError

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, mode TEXT, idempotency_key TEXT,
  decision TEXT, intent TEXT, risk TEXT, order_result TEXT
);
"""


class ExecutionPipeline:
    def __init__(self, db_path: Path, broker: Broker, risk: RiskEngine, enabled: bool, account_state_extra: dict | None = None):
        self.db = db_path
        self.broker = broker
        self.risk = risk
        self.enabled = enabled
        self.extra = account_state_extra or {}
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def audit(self, mode: str, key: str, decision: dict, intent: OrderIntent | None, risk: dict | None, result: dict | None) -> None:
        with connect(self.db) as con:
            con.execute("INSERT INTO audit (at, mode, idempotency_key, decision, intent, risk, order_result) VALUES (?,?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), mode, key, json.dumps(decision, default=str),
                         json.dumps(asdict(intent), default=str) if intent else None,
                         json.dumps(risk, default=str) if risk else None, json.dumps(result, default=str) if result else None))

    def submitted_keys(self) -> set[str]:
        with connect(self.db) as con:
            return {r[0] for r in con.execute("SELECT idempotency_key FROM audit WHERE order_result LIKE '%filled%'")}

    def execute(self, intent: OrderIntent, decision: dict, day_start_equity: float, high_water_mark: float,
                consecutive_losses: int, pip_size: float, events: list[EventWindow] | None = None) -> dict:
        try:
            acct = self.broker.account()
            q = self.broker.quote(intent.instrument)
            pos = [Position(p.instrument, 1 if p.units > 0 else -1, abs(p.units), p.avg_price, p.stop_price)
                   for p in self.broker.positions()]
        except BrokerError as e:
            self.audit("error", intent.idempotency_key, decision, intent, None, {"status": "error", "message": str(e)})
            return {"status": "error", "message": f"broker unavailable: {e}"}
        mode = acct.environment
        if mode == "live":
            self.audit("refused", intent.idempotency_key, decision, intent, None, {"status": "refused", "message": "live account"})
            raise BrokerError("broker reports a LIVE account; refusing to trade")
        state = AccountState(equity=acct.equity, day_start_equity=day_start_equity, high_water_mark=max(high_water_mark, acct.equity),
                             open_positions=pos, consecutive_losses=consecutive_losses, environment=mode,
                             account_currency=acct.currency)
        market = MarketState(q.bid, q.ask, q.time, datetime.now(timezone.utc), pip_size)
        rd = self.risk.evaluate(intent, state, market, events)
        risk = {"approved": rd.approved, "reasons": rd.reasons, "checks": rd.checks, "halted": rd.halted, "risk_amount": rd.risk_amount}
        if not rd.approved:
            self.audit(mode, intent.idempotency_key, decision, intent, risk, {"status": "not_sent"})
            return {"status": "rejected_by_risk", "reasons": rd.reasons}
        if not self.enabled:
            self.audit(mode, intent.idempotency_key, decision, intent, risk, {"status": "not_sent", "message": "execution disabled"})
            return {"status": "approved_not_sent", "message": "execution.enabled is false (dry run)"}
        res = self.broker.submit_market(intent.instrument, intent.direction * intent.units, intent.stop_price,
                                        intent.take_profit, intent.idempotency_key)
        self.audit(mode, intent.idempotency_key, decision, intent, risk, asdict(res))
        log.info("order submitted", extra={"data": {"key": intent.idempotency_key, "status": res.status, "mode": mode}})
        return {"status": res.status, "fill_price": res.fill_price, "message": res.message, "mode": mode}

    def emergency_stop(self, reason: str) -> list:
        self.risk.kill.engage(reason)
        res = self.broker.close_all()
        self.audit("emergency", "emergency", {"reason": reason}, None, None, {"closed": [asdict(r) for r in res]})
        return res
