from datetime import datetime, timedelta, timezone

import pytest

from tradeintel.execution.broker import AccountInfo, BrokerError, Quote
from tradeintel.execution.oanda import OandaPracticeBroker
from tradeintel.execution.paper import PaperBroker
from tradeintel.execution.pipeline import ExecutionPipeline
from tradeintel.risk.engine import KillSwitch, OrderIntent, RiskEngine


def fresh_quote(bid=1.1000, ask=1.1001):
    return Quote("EURUSD", bid, ask, datetime.now(timezone.utc) - timedelta(seconds=1))


def intent(key="k1", units=10000, stop=1.0951):
    return OrderIntent("EURUSD", 1, units, 1.1001, stop, None, "t", key, datetime.now(timezone.utc))


@pytest.fixture
def setup(settings, tmp_path):
    db = tmp_path / "db.sqlite"
    pb = PaperBroker(db)
    pb.set_quote(fresh_quote())
    risk = RiskEngine(settings.risk, KillSwitch(tmp_path / "KILL"))
    return db, pb, risk


def run(pipe, it):
    return pipe.execute(it, {"action": "enter_long"}, 10000, 10000, 0, 0.0001, [])


def test_dry_run_when_disabled(setup):
    db, pb, risk = setup
    out = run(ExecutionPipeline(db, pb, risk, enabled=False), intent())
    assert out["status"] == "approved_not_sent" and pb.positions() == []


def test_paper_fill_and_persistence(setup):
    db, pb, risk = setup
    out = run(ExecutionPipeline(db, pb, risk, enabled=True), intent())
    assert out["status"] == "filled" and out["fill_price"] == 1.1001
    reopened = PaperBroker(db)  # restart
    assert reopened.positions()[0].units == 10000
    assert "k1" in reopened.known_client_ids()


def test_risk_rejection_is_audited_and_not_sent(setup):
    db, pb, risk = setup
    pipe = ExecutionPipeline(db, pb, risk, enabled=True)
    out = run(pipe, intent(stop=None))
    assert out["status"] == "rejected_by_risk" and pb.positions() == []
    import sqlite3
    row = sqlite3.connect(db).execute("SELECT order_result FROM audit").fetchone()[0]
    assert "not_sent" in row


def test_duplicate_order_blocked_by_broker_and_risk(setup):
    db, pb, risk = setup
    pipe = ExecutionPipeline(db, pb, risk, enabled=True)
    assert run(pipe, intent())["status"] == "filled"
    assert run(pipe, intent())["status"] == "rejected_by_risk"
    assert pb.submit_market("EURUSD", 1, None, None, "k1").message == "duplicate client_id"
    # restart: keys reloaded from the audit log
    pipe2 = ExecutionPipeline(db, pb, RiskEngine(risk.cfg, risk.kill, seen_keys=pipe.submitted_keys()), enabled=True)
    assert "k1" in pipe2.risk._seen_keys


def test_live_account_refused(setup):
    db, pb, risk = setup

    class LiveBroker(PaperBroker):
        def account(self):
            return AccountInfo("x", "live", "USD", 1e4, 1e4)

    lb = LiveBroker(db)
    lb.set_quote(fresh_quote())
    with pytest.raises(BrokerError):
        run(ExecutionPipeline(db, lb, risk, enabled=True), intent())


def test_broker_outage_fails_safe(setup):
    db, pb, risk = setup
    pb._quotes.clear()
    out = run(ExecutionPipeline(db, pb, risk, enabled=True), intent())
    assert out["status"] == "error"


def test_emergency_stop_closes_and_engages_kill(setup):
    db, pb, risk = setup
    pipe = ExecutionPipeline(db, pb, risk, enabled=True)
    run(pipe, intent())
    pipe.emergency_stop("test")
    assert pb.positions() == [] and risk.kill.engaged()
    assert run(pipe, intent("k2"))["status"] == "rejected_by_risk"


def test_oanda_refuses_non_practice(monkeypatch):
    monkeypatch.setenv("OANDA_ENVIRONMENT", "live")
    with pytest.raises(BrokerError):
        OandaPracticeBroker(token="t", account_id="a")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    b = OandaPracticeBroker(token="t", account_id="a")
    assert "fxpractice" in b.base and "fxtrade" not in b.base


def test_oanda_requires_credentials(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    with pytest.raises(BrokerError):
        OandaPracticeBroker()


def test_order_is_audited_even_if_the_broker_call_crashes(setup):
    db, pb, risk = setup

    def boom(*a, **k):
        raise ValueError("HTML error page instead of JSON")
    pb.submit_market = boom
    with pytest.raises(ValueError):
        run(ExecutionPipeline(db, pb, risk, enabled=True), intent())
    assert "k1" in ExecutionPipeline(db, pb, risk, enabled=True).submitted_keys()  # recorded as maybe-sent
