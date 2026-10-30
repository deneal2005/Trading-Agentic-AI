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


def test_deflated_sharpe_rejects_the_luckiest_of_many_strategies_with_no_edge():
    import numpy as np

    from tradeintel.backtest.metrics import deflated_sharpe
    from tradeintel.experiments.promotion import evaluate_promotion

    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.01, size=(200, 1000))  # 200 strategies with no edge, 1000 days each
    sr = noise.mean(1) / noise.std(1, ddof=1)
    best, var = noise[sr.argmax()], float(np.var(sr, ddof=1))
    assert deflated_sharpe(best, 1, 0.0) > 0.95       # judged alone, the luckiest one looks real
    assert deflated_sharpe(best, 200, var) < 0.95     # corrected for 200 tries, it doesn't pass
    assert deflated_sharpe(rng.normal(0.002, 0.01, 1000), 200, var) > 0.95  # a genuine edge still does

    oos = {"n_trades": 100, "sharpe": 2.0, "sharpe_ci90": [0.5, 3], "max_drawdown": 0.05, "profit_factor": 2.0, "net_return": 0.5}
    base = {b: {"sharpe": 0.0, "net_return": 0.0} for b in ("no_trade", "buy_and_hold")}
    from tradeintel.config import PromotionCfg
    cfg = PromotionCfg(min_trades=60, min_oos_sharpe=0.5, max_oos_drawdown=0.15, min_profit_factor=1.1,
                       must_beat_baselines=["no_trade", "buy_and_hold"], max_cost_stress_multiplier_loss=2.0)
    g = evaluate_promotion(oos, base, {"net_return": 0.1}, cfg, False, {"value": 0.6, "n_trials": 200})
    assert not g.passed and g.checks["deflated_sharpe"] is False and "may be luck" in g.reasons[0]
