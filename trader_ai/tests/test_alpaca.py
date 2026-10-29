import json
import os
from dataclasses import asdict
from datetime import timedelta

import pytest
import requests

from tradeintel.config import load_dotenv
from tradeintel.data.providers import AlpacaProvider, DataUnavailable
from tradeintel.execution.alpaca import AlpacaPaperBroker
from tradeintel.execution.broker import BrokerError, OrderResult
from tradeintel.execution.pipeline import ExecutionPipeline
from tradeintel.risk.engine import AccountState, EventWindow, KillSwitch, MarketState, OrderIntent, RiskEngine
from conftest import NOW


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.content = json.dumps(body).encode()

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Answers by (method, path suffix); records every call."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, suffix), resp in self.routes.items():
            if m == method and url.endswith(suffix):
                return resp.pop(0) if isinstance(resp, list) else resp
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def broker(routes):
    return AlpacaPaperBroker("k", "s", session=FakeSession(routes), quote_pause=0)


def iex(bp, ap):
    return Resp({"quote": {"bp": bp, "ap": ap, "t": "2026-10-29T13:36:00Z"}})


def test_quote_uses_tightest_valid_snapshot():
    # the sequence seen live on SPY right after the open
    b = broker({("GET", "/quotes/latest"): [iex(767.0, 767.73), iex(767.78, 767.81), iex(767.8, 767.88)]})
    q = b.quote("SPY")
    assert (q.bid, q.ask) == (767.78, 767.81)
    with pytest.raises(BrokerError):  # zero/crossed books only -> no quote, so no order
        broker({("GET", "/quotes/latest"): [iex(0, 767.8), iex(768.0, 767.9), iex(0, 0)]}).quote("SPY")


def test_requires_keys(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(BrokerError):
        AlpacaPaperBroker()


def test_paper_host_only_and_refuses_non_paper_account():
    b = broker({("GET", "/v2/account"): Resp({"account_number": "123456", "currency": "USD", "cash": "1", "equity": "1"})})
    assert b.base == "https://paper-api.alpaca.markets"
    with pytest.raises(BrokerError):
        b.account()
    b = broker({("GET", "/v2/account"): Resp({"account_number": "PA1234", "currency": "USD", "cash": "9", "equity": "10"})})
    a = b.account()
    assert (a.environment, a.balance, a.equity) == ("practice", 9.0, 10.0)


def test_positions_signed_by_side_and_quote():
    b = broker({("GET", "/v2/positions"): Resp([{"symbol": "SPY", "qty": "5", "side": "short", "avg_entry_price": "500.1"}]),
                ("GET", "/quotes/latest"): Resp({"quote": {"bp": 500.0, "ap": 500.02, "t": "2026-10-29T15:00:00.123456789Z"}})})
    assert b.positions()[0].units == -5
    q = b.quote("SPY")
    assert (q.bid, q.ask, q.time.microsecond) == (500.0, 500.02, 123456)


def test_submit_sends_gtc_oto_stop_and_maps_results():
    s = FakeSession({("POST", "/v2/orders"): [Resp({"id": "o1", "status": "accepted"}),
                                              Resp({"message": "client_order_id must be unique"}, 422)]})
    b = AlpacaPaperBroker("k", "s", session=s)
    r = b.submit_market("SPY", -12.0, 512.3456, None, "key1")
    assert s.calls[0][2]["json"] == {"symbol": "SPY", "qty": "12", "side": "sell", "type": "market", "time_in_force": "gtc",
                                     "client_order_id": "key1", "stop_loss": {"stop_price": "512.35"}, "order_class": "oto"}
    assert (r.status, r.broker_order_id) == ("pending", "o1")
    assert b.submit_market("SPY", 12, 500, None, "key1").status == "rejected"


def test_http_error_becomes_broker_error():
    with pytest.raises(BrokerError):
        broker({("GET", "/v2/positions"): Resp({}, 500)}).positions()


def test_provider_paginates_and_requests_adjusted_bars(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")

    def bar(t, c):
        return {"t": t, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 10}

    s = FakeSession({("GET", "/v2/stocks/SPY/bars"): [
        Resp({"bars": [bar("2024-01-02T05:00:00Z", 470)], "next_page_token": "p2"}),
        Resp({"bars": [bar("2024-01-03T05:00:00Z", 468)], "next_page_token": None})]})
    df = AlpacaProvider(session=s).fetch("SPY", "1d", "2024-01-01")
    assert list(df["close"]) == [470, 468] and str(df.index[0]) == "2024-01-02 00:00:00+00:00"
    assert s.calls[0][2]["params"]["adjustment"] == "all" and s.calls[1][2]["params"]["page_token"] == "p2"
    monkeypatch.delenv("APCA_API_KEY_ID")
    with pytest.raises(DataUnavailable):
        AlpacaProvider(session=s).fetch("SPY", "1d", "2024-01-01")


def test_load_dotenv_handles_bom_comments_and_never_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TI_TEST_KEEP", "real")
    monkeypatch.delenv("TI_TEST_NEW", raising=False)
    p = tmp_path / ".env"
    p.write_text("﻿# comment\nTI_TEST_NEW=\"abc\"\nTI_TEST_KEEP=from_file\n\n", encoding="utf-8")
    load_dotenv(p)
    assert os.environ["TI_TEST_NEW"] == "abc" and os.environ["TI_TEST_KEEP"] == "real"
    monkeypatch.delenv("TI_TEST_NEW")


def test_usd_event_blocks_spy_order(settings, tmp_path):
    eng = RiskEngine(settings.risk, KillSwitch(tmp_path / "KILL"))
    o = OrderIntent("SPY", 1, 10, 500.02, 490.0, None, "t", "k", NOW)
    a = AccountState(equity=100000, day_start_equity=100000, high_water_mark=100000)
    m = MarketState(500.0, 500.02, NOW - timedelta(seconds=2), NOW, 0.01)
    assert eng.evaluate(o, a, m).approved
    d = eng.evaluate(o, a, m, [EventWindow(NOW + timedelta(minutes=5), ("USD",), "CPI")])
    assert not d.checks["event_blackout"]


def test_pending_orders_count_as_submitted_after_restart(tmp_path, settings):
    pipe = ExecutionPipeline(tmp_path / "db.sqlite", None, RiskEngine(settings.risk, KillSwitch(tmp_path / "K")), True)
    pipe.audit("practice", "k-pending", {}, None, None, asdict(OrderResult("pending", "o1", None, 0, "accepted")))
    pipe.audit("practice", "k-notsent", {}, None, None, {"status": "not_sent"})
    assert pipe.submitted_keys() == {"k-pending"}
