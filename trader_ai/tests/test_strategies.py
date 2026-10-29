import pandas as pd
import pytest

from tradeintel.strategies.library import REGISTRY, build


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_signals_are_causal(name, synth_bars):
    """The signal at bar t must not change when future bars are removed."""
    strat = build(name)
    full = strat.signals(synth_bars)
    for cut in (300, 650, 1000):
        part = strat.signals(synth_bars.iloc[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut], part, check_freq=False)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_signal_frame_shape(name, synth_bars):
    sig = build(name).signals(synth_bars)
    assert list(sig.columns) == ["target", "stop_dist", "tp_dist", "size_frac"]
    assert set(sig["target"].unique()) <= {-1, 0, 1}
    if REGISTRY[name].requires_stop:
        active = sig[sig["target"] != 0]
        assert (active["stop_dist"] > 0).all()


def test_grid_respects_constraints():
    for g in build("sma_cross").grid():
        assert g["fast"] < g["slow"]


def test_turn_of_month_holds_last_day_through_first_days():
    from conftest import make_bars
    bars = make_bars([100.0] * 45, start="2024-01-01", freq="B")  # Jan 1 .. Mar 1 business days
    tgt = build("turn_of_month", days_after=3).signals(bars)["target"]
    on = [d.strftime("%m-%d") for d in tgt[tgt == 1].index]
    # signal on the last trading day and the first 2 of the month -> held days 1..3 after next-open fills
    assert on == ["01-01", "01-02", "01-31", "02-01", "02-02", "02-29", "03-01"]


def test_trend_filter_and_tsmom_are_long_or_flat(synth_bars):
    for name in ("sma_trend_filter", "tsmom"):
        assert set(build(name).signals(synth_bars)["target"].unique()) <= {0, 1}


def test_unknown_param_rejected():
    with pytest.raises(ValueError):
        build("sma_cross", nonsense=1)
