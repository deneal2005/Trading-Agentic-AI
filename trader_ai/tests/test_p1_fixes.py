"""Regression tests for the P1 findings of audit/AUDIT_2026-10-29.md (F-01 to F-06)."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from tradeintel.agents.coordinator import DecisionLog
from tradeintel.backtest.research import holdout_test
from tradeintel.data.store import BarStore
from tradeintel.data.validate import drop_unfinished
from tradeintel.execution.broker import BrokerPosition, Quote
from tradeintel.execution.paper import PaperBroker
from tradeintel.experiments.registry import Registry
from tradeintel.news.sources import FetchHealth
from tradeintel.news.store import NewsStore
from tradeintel.risk.engine import AccountState, KillSwitch, MarketState, OrderIntent, RiskEngine
from tradeintel.service import StateStore, calendar_fresh, run_cycle, strategy_promoted, update_loss_streak
from tradeintel.strategies.library import build

UTC = timezone.utc
DEFAULT = build("sma_cross").params


def _record(reg, kind, verdict, inst="EURUSD", tf="1d", params=None, folds=None, src="yahoo"):
    p = build("sma_cross", **(params or {})).params
    metrics = {"folds": [{"params": f} for f in (folds or [p, p])]} if kind == "walkforward" else {}
    reg.record(kind, strategy="sma_cross", params=p, instrument=inst, timeframe=tf, data_source=src, data_hash="h",
               config={}, n_trials=1, metrics=metrics, verdict=verdict)


# F-01, F-02 ---------------------------------------------------------------

def test_approval_needs_same_market_settings_real_data_fixed_folds_and_a_passed_holdout(settings):
    reg = Registry(settings.paths.db_path)
    for kw in ({"inst": "GBPUSD"}, {"tf": "1h"}, {"params": {"fast": 50, "slow": 200}}, {"src": "SYNTHETIC"}):
        _record(reg, "walkforward", "promotable", **kw)
        _record(reg, "holdout", "passed", **kw)
    # optimised walk-forward: some folds traded other settings than the live ones
    _record(reg, "walkforward", "promotable", folds=[DEFAULT, {**DEFAULT, "fast": 10}])
    assert not strategy_promoted(settings, "sma_cross")
    _record(reg, "walkforward", "promotable")          # fixed-settings walk-forward passed ...
    assert not strategy_promoted(settings, "sma_cross")  # ... but no holdout yet
    _record(reg, "holdout", "failed")
    assert not strategy_promoted(settings, "sma_cross")
    _record(reg, "holdout", "passed")
    assert strategy_promoted(settings, "sma_cross")


def test_holdout_records_a_verdict(settings, synth_bars):
    reg = Registry(settings.paths.db_path)
    m = holdout_test(settings, synth_bars, "sma_cross", {}, reg)
    row = reg.list(1, kind="holdout")[0]
    assert m["verdict"] in ("passed", "failed") and row["verdict"] == m["verdict"] and row["params"] == DEFAULT


# F-03 ---------------------------------------------------------------------

def test_unfinished_daily_bars_are_dropped():
    idx = pd.date_range("2026-10-27", periods=3, freq="D", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)
    kept, n = drop_unfinished(df, "1d", "equity", pd.Timestamp("2026-10-29 15:22", tz="UTC"))  # US session still open
    assert n == 1 and kept.index[-1] == idx[1]
    after_close = pd.Timestamp("2026-10-29 21:05", tz="UTC")
    assert drop_unfinished(df, "1d", "equity", after_close)[1] == 0
    assert drop_unfinished(df, "1d", "forex", after_close)[1] == 1  # an FX day runs to midnight UTC


def test_run_cycle_ignores_a_stored_unfinished_bar(settings, synth_bars):
    BarStore(settings.paths.db_path).upsert(synth_bars, "EURUSD", "1d", "SYNTHETIC")
    now = synth_bars.index[-1].to_pydatetime() + timedelta(hours=2)  # the last stored bar's day is still running
    pb = PaperBroker(settings.paths.db_path)
    last = float(synth_bars["close"].iloc[-1])
    pb.set_quote(Quote("EURUSD", last, last + 0.0001, now))
    run_cycle(settings, broker=pb, now=now)
    used = json.loads(DecisionLog(settings.paths.db_path).recent(1)[0]["inputs"])["last_bar"]
    assert used == str(synth_bars.index[-2])


# F-04 ---------------------------------------------------------------------

def test_loss_streak_counts_losing_round_trips_and_resets_on_a_win(tmp_path):
    st = StateStore(tmp_path / "db.sqlite")
    long_ = BrokerPosition("SPY", 10, 100.0, None)
    update_loss_streak(st, "SPY", long_, 100.0)
    update_loss_streak(st, "SPY", None, 95.0)  # closed below entry
    assert st.get("consecutive_losses") == 1
    update_loss_streak(st, "SPY", BrokerPosition("SPY", -10, 100.0, None), 100.0)
    update_loss_streak(st, "SPY", None, 102.0)  # short closed higher
    assert st.get("consecutive_losses") == 2
    update_loss_streak(st, "SPY", long_, 100.0)
    update_loss_streak(st, "SPY", None, None)  # no price available: judged on a later cycle
    assert st.get("consecutive_losses") == 2 and st.get("open_trade")
    update_loss_streak(st, "SPY", None, 104.0)  # a win resets the streak
    assert st.get("consecutive_losses") == 0 and st.get("open_trade") is None


# F-05 ---------------------------------------------------------------------

def test_hold_cycle_records_baseline_and_a_loss_engages_the_kill_switch(settings, synth_bars):
    settings.live.strategy = "no_trade"  # always flat -> every cycle is a 'hold'
    BarStore(settings.paths.db_path).upsert(synth_bars, "EURUSD", "1d", "SYNTHETIC")
    now = synth_bars.index[-1].to_pydatetime() + timedelta(hours=26)
    pb = PaperBroker(settings.paths.db_path)
    out = run_cycle(settings, broker=pb, now=now)
    st = StateStore(settings.paths.db_path)
    assert out["decision"] == "hold" and st.get("day_start_equity") == 10_000 and st.get("hwm") == 10_000
    st.set("day_start_equity", 10_500)  # equity is now 4.8% below the day's start (limit 2%)
    out = run_cycle(settings, broker=pb, now=now)
    assert KillSwitch(settings.paths.kill_switch_file).engaged() and "daily loss" in out["halted"]


# F-06 ---------------------------------------------------------------------

def test_missing_calendar_blocks_new_entries(settings, tmp_path):
    t = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
    o = OrderIntent("EURUSD", 1, 10000, 1.1001, 1.0951, None, "t", "k", t)
    a = AccountState(equity=10000, day_start_equity=10000, high_water_mark=10000)
    m = MarketState(1.1000, 1.1001, t - timedelta(seconds=2), t, 0.0001)
    d = RiskEngine(settings.risk, KillSwitch(tmp_path / "K1")).evaluate(o, a, m)  # calendar unknown
    assert not d.approved and not d.checks["event_calendar"]
    assert RiskEngine(settings.risk, KillSwitch(tmp_path / "K2")).evaluate(o, a, m, []).approved  # current calendar, nothing scheduled


def test_calendar_is_fresh_only_after_a_successful_fetch_this_week(settings):
    ns = NewsStore(settings.paths.db_path)
    thursday = datetime(2026, 10, 29, 12, tzinfo=UTC)  # the week began on Sunday 25 October
    assert not calendar_fresh(ns, thursday)
    ns.record_health(FetchHealth("forexfactory", True, 0.1, 50, None, datetime(2026, 10, 24, 23, tzinfo=UTC)))  # last week's export
    assert not calendar_fresh(ns, thursday)
    ns.record_health(FetchHealth("forexfactory", True, 0.1, 50, None, datetime(2026, 10, 26, 8, tzinfo=UTC)))
    assert calendar_fresh(ns, thursday)
    ns.record_health(FetchHealth("forexfactory", False, 0.1, 0, "timeout", datetime(2026, 10, 28, 8, tzinfo=UTC)))
    assert calendar_fresh(ns, thursday)  # a later failed retry doesn't void this week's good fetch
