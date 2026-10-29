import pytest

from tradeintel.backtest.research import engine_params, holdout_test, research
from tradeintel.backtest.walkforward import split_holdout, walk_forward
from tradeintel.experiments.registry import Registry
from tradeintel.strategies.library import build


def test_folds_are_chronological_and_disjoint(settings, synth_bars):
    wf = walk_forward(build("sma_cross"), synth_bars, settings.costs, engine_params(settings), 300, 100)
    assert len(wf.folds) == (1200 - 300) // 100
    for f in wf.folds:
        assert f.train_end < f.test_start
        assert f.oos.equity.index[0] == f.test_start
    for a, b in zip(wf.folds, wf.folds[1:]):
        assert a.test_end < b.test_start
    assert wf.oos_equity.index.is_monotonic_increasing and wf.oos_equity.index.is_unique
    assert wf.n_trials == len(wf.folds) * len(build("sma_cross").grid())


def test_research_never_touches_holdout(settings, synth_bars):
    rep = research(settings, synth_bars, "sma_cross", optimize=False)
    _, hold = split_holdout(synth_bars, settings.backtest.holdout_fraction)
    assert rep["oos_equity"].index.max() < hold.index[0]
    assert rep["synthetic"] and not rep["gate"]["passed"]
    assert "results are on SYNTHETIC data and cannot be promoted" in rep["gate"]["reasons"]


def test_research_recorded_and_holdout_single_use(settings, synth_bars):
    reg = Registry(settings.paths.db_path)
    rep = research(settings, synth_bars, "sma_cross", registry=reg, optimize=False)
    assert reg.list(5)[0]["id"] == rep["experiment_id"]
    holdout_test(settings, synth_bars, "sma_cross", {}, reg)
    with pytest.raises(PermissionError):
        holdout_test(settings, synth_bars, "sma_cross", {}, reg)
