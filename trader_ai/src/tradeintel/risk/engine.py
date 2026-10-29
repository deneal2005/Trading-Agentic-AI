"""Deterministic risk enforcement layer.

Nothing upstream (strategy, model, LLM, agent, news) can override a
rejection. Missing or inconsistent inputs reject the order. Breaches of
loss or drawdown limits engage a persistent kill switch that survives
restarts and must be cleared by a human (`tradeintel risk reset`).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import RiskCfg


@dataclass
class Position:
    instrument: str
    direction: int
    units: float
    entry_price: float
    stop_price: float | None


@dataclass
class OrderIntent:
    instrument: str
    direction: int
    units: float
    entry_price: float
    stop_price: float | None
    take_profit: float | None
    strategy: str
    idempotency_key: str
    created_at: datetime


@dataclass
class AccountState:
    equity: float
    day_start_equity: float
    high_water_mark: float
    open_positions: list[Position] = field(default_factory=list)
    consecutive_losses: int = 0
    environment: str = "paper"      # paper | practice | live
    account_currency: str = "USD"


@dataclass
class MarketState:
    bid: float
    ask: float
    quote_time: datetime
    now: datetime
    pip_size: float
    max_quote_age: timedelta = timedelta(seconds=30)


@dataclass
class EventWindow:
    """A scheduled high-impact event that affects one or more currencies."""

    time: datetime
    currencies: tuple[str, ...]
    title: str


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str]
    checks: dict[str, bool]
    halted: bool = False
    risk_amount: float | None = None


def currency_legs(instrument: str, direction: int) -> dict[str, int]:
    """EURUSD long -> {EUR: +1, USD: -1}."""
    if len(instrument) == 6 and instrument.isalpha():
        return {instrument[:3]: direction, instrument[3:]: -direction}
    return {instrument: direction}


def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


class KillSwitch:
    def __init__(self, path: Path):
        self.path = path

    def engaged(self) -> bool:
        return self.path.exists()

    def reason(self) -> str:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("reason", "unknown")
        except Exception:
            return "kill switch file present"

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"reason": reason, "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()


class RiskEngine:
    def __init__(self, cfg: RiskCfg, kill_switch: KillSwitch,
                 allowed_environments: tuple[str, ...] = ("paper", "practice"),
                 seen_keys: set[str] | None = None):
        self.cfg = cfg
        self.kill = kill_switch
        self.allowed_env = allowed_environments
        # Pass keys persisted by the execution layer so duplicate protection
        # survives restarts.
        self._seen_keys: set[str] = set(seen_keys or ())

    def to_account(self, amount_quote: float, instrument: str, price: float, account_ccy: str) -> float | None:
        if len(instrument) == 6:
            if instrument[3:] == account_ccy:
                return amount_quote
            if instrument[:3] == account_ccy:
                return amount_quote / price
            return None  # cross conversion not supported -> caller rejects
        return amount_quote

    def evaluate(self, o: OrderIntent, a: AccountState, m: MarketState, events: list[EventWindow] | None = None) -> RiskDecision:
        c = self.cfg
        checks: dict[str, bool] = {}
        reasons: list[str] = []
        halt_reason: str | None = None

        def check(name: str, ok: bool, why: str) -> bool:
            checks[name] = bool(ok)
            if not ok:
                reasons.append(why)
            return ok

        # 0) kill switch and environment
        if not check("kill_switch_clear", not self.kill.engaged(), f"kill switch engaged: {self.kill.reason()}"):
            return RiskDecision(False, reasons, checks, halted=True)
        check("environment", a.environment in self.allowed_env, f"account environment '{a.environment}' is not permitted")

        # 1) input completeness
        essentials = [o.units, o.entry_price, a.equity, a.day_start_equity, a.high_water_mark, m.bid, m.ask]
        if not check("inputs_present", not any(_bad(x) for x in essentials) and o.direction in (1, -1),
                     "essential risk inputs missing or invalid"):
            return RiskDecision(False, reasons, checks)
        check("positive_values", o.units > 0 and o.entry_price > 0 and a.equity > 0 and m.bid > 0 and m.ask >= m.bid,
              "non-positive or inconsistent values (units/price/equity/bid/ask)")

        # 2) account-level circuit breakers
        daily_loss = (a.day_start_equity - a.equity) / a.day_start_equity if a.day_start_equity > 0 else 1.0
        if not check("daily_loss", daily_loss < c.max_daily_loss, f"daily loss {daily_loss:.2%} >= limit {c.max_daily_loss:.2%}"):
            halt_reason = "max daily loss reached"
        dd = (a.high_water_mark - a.equity) / a.high_water_mark if a.high_water_mark > 0 else 1.0
        if not check("drawdown", dd < c.max_drawdown, f"drawdown {dd:.2%} >= limit {c.max_drawdown:.2%}"):
            halt_reason = "max drawdown reached"
        check("consecutive_losses", a.consecutive_losses < c.max_consecutive_losses,
              f"{a.consecutive_losses} consecutive losses (limit {c.max_consecutive_losses}); pause and review")

        # 3) market data quality
        age = m.now - m.quote_time
        check("quote_fresh", timedelta(0) <= age <= m.max_quote_age, f"quote is stale or from the future (age {age})")
        spread_pips = (m.ask - m.bid) / m.pip_size
        check("spread", spread_pips <= c.max_spread_pips, f"spread {spread_pips:.1f} pips > {c.max_spread_pips}")
        ref = m.ask if o.direction == 1 else m.bid
        check("price_consistent", abs(o.entry_price - ref) / ref < 0.01, "order price deviates >1% from current quote")

        # 4) trade-level risk
        risk_amt = None
        if o.stop_price is None or _bad(o.stop_price):
            check("stop_loss", not c.require_stop_loss, "stop-loss is required")
        else:
            correct_side = (o.direction == 1 and o.stop_price < o.entry_price) or (o.direction == -1 and o.stop_price > o.entry_price)
            if check("stop_side", correct_side, "stop-loss is on the wrong side of entry"):
                risk_q = abs(o.entry_price - o.stop_price) * o.units
                risk_amt = self.to_account(risk_q, o.instrument, o.entry_price, a.account_currency)
                if check("risk_convertible", risk_amt is not None, "cannot convert risk to account currency"):
                    check("risk_per_trade", risk_amt <= c.max_risk_per_trade * a.equity * 1.0001,
                          f"trade risk {risk_amt:.2f} > {c.max_risk_per_trade:.2%} of equity")
                    open_risk = 0.0
                    for p in a.open_positions:
                        if p.stop_price is None:
                            open_risk = float("inf")
                            break
                        r = self.to_account(abs(p.entry_price - p.stop_price) * p.units, p.instrument, p.entry_price, a.account_currency)
                        open_risk += r if r is not None else float("inf")
                    check("aggregate_risk", open_risk + risk_amt <= c.max_open_risk * a.equity * 1.0001,
                          f"aggregate open risk would be {(open_risk + risk_amt) / a.equity:.2%} > {c.max_open_risk:.2%}")

        def notional(inst: str, units: float, price: float) -> float:
            if len(inst) == 6 and inst[:3] == a.account_currency:
                return units
            n = self.to_account(units * price, inst, price, a.account_currency)
            return float("inf") if n is None else n

        existing = sum(notional(p.instrument, p.units, p.entry_price) for p in a.open_positions)
        lev = (notional(o.instrument, o.units, o.entry_price) + existing) / a.equity
        check("leverage", lev <= c.max_leverage, f"leverage would be {lev:.1f}x > {c.max_leverage}x")
        check("max_units", o.units <= c.max_position_units, f"units {o.units} > {c.max_position_units}")
        same = sum(1 for p in a.open_positions if p.instrument == o.instrument)
        check("positions_per_instrument", same < c.max_positions_per_instrument,
              f"already {same} position(s) in {o.instrument}")
        legs = currency_legs(o.instrument, o.direction)
        for ccy, d in legs.items():
            n = sum(1 for p in a.open_positions if currency_legs(p.instrument, p.direction).get(ccy) == d)
            check(f"correlated_{ccy}", n < c.max_correlated_exposure,
                  f"{n} open positions already {'long' if d > 0 else 'short'} {ccy}")

        # 5) event risk. events=None means the calendar is unknown (missing or stale): fail closed.
        # An empty list means the calendar is current and has nothing scheduled.
        check("event_calendar", events is not None, "economic calendar missing or out of date; no new entries until it is refreshed")
        window = timedelta(minutes=c.event_blackout_minutes)
        # A single-leg instrument (stock/ETF) is priced in the account currency, so that currency's events apply.
        affected = set(legs) if len(legs) > 1 else set(legs) | {a.account_currency}
        blocking = [e for e in (events or []) if abs(e.time - m.now) <= window and set(e.currencies) & affected]
        check("event_blackout", not blocking, f"inside blackout for: {[e.title for e in blocking][:3]}")

        # 6) duplicate protection
        check("not_duplicate", o.idempotency_key not in self._seen_keys, f"duplicate order key {o.idempotency_key}")

        if halt_reason:
            self.kill.engage(halt_reason)
        approved = all(checks.values())
        if approved:
            self._seen_keys.add(o.idempotency_key)
        return RiskDecision(approved, reasons, checks, halted=halt_reason is not None, risk_amount=risk_amt)
