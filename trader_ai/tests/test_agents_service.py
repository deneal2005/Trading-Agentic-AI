from datetime import timedelta

import pandas as pd

from tradeintel.agents.coordinator import DecisionContext, coordinate
from tradeintel.data.store import BarStore
from tradeintel.execution.broker import Quote
from tradeintel.execution.paper import PaperBroker
from tradeintel.models.forecast import ModelRegistry, walk_forward_eval
from tradeintel.service import run_cycle
from tradeintel.strategies.base import frame
from conftest import make_bars


def ctx_for(target, now_offset=timedelta(days=1), events=None, position=0, regime="up_lowvol", prob=None):
    b = make_bars([1.0] * 5, start="2026-10-20")
    sig = frame(b.index, pd.Series([0, 0, 0, 0, target], index=b.index), stop_dist=0.01)
    now = b.index[-1].to_pydatetime() + now_offset
    return DecisionContext("EURUSD", b, sig, "sma_cross", now, timedelta(days=4), position, events or [], prob,
                           regime=regime)


def test_enter_when_clean():
    d = coordinate(ctx_for(1))
    assert d.action == "enter_long"
    assert [r.agent for r in d.reports] == ["market", "news", "quant", "adversarial"]


def test_single_mode_baseline_ignores_other_agents():
    assert coordinate(ctx_for(1, regime="flat_lowvol"), "single").action == "enter_long"


def test_abstain_on_stale_data():
    assert coordinate(ctx_for(1, now_offset=timedelta(days=10))).action == "abstain"


def test_upcoming_event_blocks_entry_but_not_exit():
    c = ctx_for(1)
    ev = [{"impact": "high", "currency": "USD", "title": "NFP", "event_time": (c.now + timedelta(hours=2)).isoformat()}]
    d = coordinate(ctx_for(1, events=ev))
    assert d.action == "abstain" and "NFP" in d.reasons[0]
    assert coordinate(ctx_for(0, events=ev, position=1)).action == "exit"


def test_disagreeing_model_blocks_entry():
    assert coordinate(ctx_for(1, prob=0.3)).action == "abstain"
    assert coordinate(ctx_for(1, prob=0.52)).action == "enter_long"


def test_hold_when_unchanged():
    assert coordinate(ctx_for(1, position=1)).action == "hold"


def test_run_cycle_dry_run_and_restart_safe(settings, synth_bars):
    bars = synth_bars.copy()
    BarStore(settings.paths.db_path).upsert(bars, "EURUSD", "1d", "SYNTHETIC")
    now = bars.index[-1].to_pydatetime() + timedelta(hours=26)
    pb = PaperBroker(settings.paths.db_path)
    last = float(bars["close"].iloc[-1])
    pb.set_quote(Quote("EURUSD", last, last + 0.0001, now))
    out1 = run_cycle(settings, broker=pb, now=now)
    out2 = run_cycle(settings, broker=pb, now=now)
    assert out1["decision"] in ("enter_long", "enter_short", "hold", "abstain", "exit")
    assert pb.positions() == []  # execution disabled by default: nothing sent
    from tradeintel.agents.coordinator import DecisionLog
    assert len(DecisionLog(settings.paths.db_path).recent()) == 2


def test_run_cycle_without_data_abstains(settings):
    assert run_cycle(settings, broker=PaperBroker(settings.paths.db_path))["status"] == "abstain"


def test_model_on_random_walk_is_rejected(tmp_path, synth_bars):
    ev, model = walk_forward_eval(synth_bars, 300, 100)
    assert ev.verdict == "rejected" and ev.n_folds == 6  # 252-bar momentum warmup leaves room for 6 folds
    reg = ModelRegistry(tmp_path)
    reg.save("m", model, {"verdict": ev.verdict})
    assert reg.load_latest_accepted("m") == (None, None)
    assert len(reg.versions("m")) == 1


def test_single_instance_lock_blocks_second_holder(tmp_path):
    import pytest
    from tradeintel.service import single_instance

    with single_instance(tmp_path / "cycle.lock"):
        with pytest.raises(RuntimeError):
            with single_instance(tmp_path / "cycle.lock"):
                pass
    with single_instance(tmp_path / "cycle.lock"):  # released after exit
        pass
