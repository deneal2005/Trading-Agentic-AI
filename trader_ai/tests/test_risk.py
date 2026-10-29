from dataclasses import replace
from datetime import timedelta

import pytest

from tradeintel.risk.engine import (AccountState, EventWindow, KillSwitch, MarketState, OrderIntent, Position,
                                    RiskEngine, currency_legs)
from conftest import NOW


@pytest.fixture
def engine(settings, tmp_path):
    return RiskEngine(settings.risk, KillSwitch(tmp_path / "KILL"))


def intent(**kw):
    base = dict(instrument="EURUSD", direction=1, units=10000, entry_price=1.1001, stop_price=1.0951, take_profit=None,
                strategy="t", idempotency_key="k1", created_at=NOW)
    base.update(kw)
    return OrderIntent(**base)


def acct(**kw):
    base = dict(equity=10000, day_start_equity=10000, high_water_mark=10000, environment="paper")
    base.update(kw)
    return AccountState(**base)


MKT = MarketState(bid=1.1000, ask=1.1001, quote_time=NOW - timedelta(seconds=2), now=NOW, pip_size=0.0001)


def test_valid_order_approved(engine):
    d = engine.evaluate(intent(), acct(), MKT)
    assert d.approved, d.reasons
    assert d.risk_amount == pytest.approx(50.0)


@pytest.mark.parametrize("kw,check", [
    ({"stop_price": None}, "stop_loss"),
    ({"stop_price": 1.11}, "stop_side"),
    ({"units": 20000}, "risk_per_trade"),
    ({"units": float("nan")}, "inputs_present"),
    ({"direction": 0}, "inputs_present"),
    ({"entry_price": 1.2}, "price_consistent"),
    ({"units": 600000, "stop_price": 1.1000}, "max_units"),
])
def test_order_rejections(engine, kw, check):
    d = engine.evaluate(intent(**kw), acct(), MKT)
    assert not d.approved and d.checks.get(check) is False, d.checks


def test_stale_quote_and_wide_spread(engine):
    assert not engine.evaluate(intent(), acct(), replace(MKT, quote_time=NOW - timedelta(minutes=5))).checks["quote_fresh"]
    assert not engine.evaluate(intent(), acct(), replace(MKT, ask=1.1010)).checks["spread"]


def test_daily_loss_engages_persistent_kill_switch(settings, tmp_path):
    ks = KillSwitch(tmp_path / "KILL")
    e = RiskEngine(settings.risk, ks)
    d = e.evaluate(intent(), acct(equity=9750, high_water_mark=10000), MKT)
    assert not d.approved and d.halted and ks.engaged()
    # a fresh engine (restart) still refuses everything
    d2 = RiskEngine(settings.risk, KillSwitch(tmp_path / "KILL")).evaluate(intent(idempotency_key="k2"), acct(), MKT)
    assert not d2.approved and "kill switch" in d2.reasons[0]


def test_drawdown_halts(engine):
    d = engine.evaluate(intent(), acct(equity=8900, day_start_equity=8900, high_water_mark=10000), MKT)
    assert d.halted and not d.checks["drawdown"]


def test_live_environment_refused(engine):
    assert not engine.evaluate(intent(), acct(environment="live"), MKT).checks["environment"]


def test_duplicate_key_refused(engine):
    assert engine.evaluate(intent(), acct(), MKT).approved
    assert not engine.evaluate(intent(), acct(), MKT).checks["not_duplicate"]


def test_duplicate_keys_survive_restart(settings, tmp_path):
    e = RiskEngine(settings.risk, KillSwitch(tmp_path / "K"), seen_keys={"k1"})
    assert not e.evaluate(intent(), acct(), MKT).approved


def test_event_blackout(engine):
    ev = [EventWindow(NOW + timedelta(minutes=10), ("USD",), "Non-Farm Payrolls")]
    assert not engine.evaluate(intent(), acct(), MKT, ev).checks["event_blackout"]
    far = [EventWindow(NOW + timedelta(hours=3), ("USD",), "NFP")]
    other = [EventWindow(NOW + timedelta(minutes=5), ("JPY",), "BoJ")]
    assert engine.evaluate(intent(), acct(), MKT, far + other).approved


def test_aggregate_and_correlated_exposure(engine):
    open_pos = [Position("GBPUSD", 1, 10000, 1.30, 1.2950), Position("AUDUSD", 1, 10000, 0.66, 0.6550)]
    d = engine.evaluate(intent(), acct(open_positions=open_pos), MKT)
    assert not d.checks["correlated_USD"]  # already short USD twice
    unstopped = [Position("GBPJPY", 1, 1000, 190.0, None)]
    assert not engine.evaluate(intent(idempotency_key="z"), acct(open_positions=unstopped), MKT).checks["aggregate_risk"]


def test_consecutive_losses_pause(engine):
    assert not engine.evaluate(intent(), acct(consecutive_losses=5), MKT).checks["consecutive_losses"]


def test_currency_legs():
    assert currency_legs("EURUSD", -1) == {"EUR": -1, "USD": 1}
