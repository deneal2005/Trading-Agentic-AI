"""Walk-forward validation with an untouched holdout.

The dataset is split chronologically into a research region and a final
holdout. Within the research region, rolling folds select parameters on the
train window (in-sample) and evaluate them on the following test window
(out-of-sample). Indicators are causal and test trading starts flat, so no
information crosses from test back into selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..config import Costs
from ..strategies.base import Strategy
from .engine import BacktestResult, EngineParams, run_backtest
from .metrics import _sharpe, PERIODS_PER_YEAR


def split_holdout(bars: pd.DataFrame, holdout_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    cut = int(len(bars) * (1 - holdout_fraction))
    return bars.iloc[:cut], bars.iloc[cut:]


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen_params: dict
    is_sharpe: float
    oos: BacktestResult


@dataclass
class WalkForwardResult:
    strategy: str
    folds: list[Fold]
    oos_equity: pd.Series
    oos_trades: list
    n_trials: int
    meta: dict = field(default_factory=dict)


SignalFilter = Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame]


def _eval(strategy_cls, params, bars, costs, ep, start=None, signal_filter: SignalFilter | None = None):
    strat = strategy_cls(**params)
    sig = strat.signals(bars)
    if signal_filter is not None:
        sig = signal_filter(bars, sig)
    ep2 = EngineParams(**{**ep.__dict__, "require_stop": strat.requires_stop})
    return run_backtest(bars, sig, costs, ep2, trade_start=start)


def walk_forward(
    strategy: Strategy,
    bars: pd.DataFrame,
    costs: Costs,
    ep: EngineParams,
    train_bars: int,
    test_bars: int,
    timeframe: str = "1d",
    min_is_trades: int = 5,
    optimize: bool = True,
    signal_filter: SignalFilter | None = None,
) -> WalkForwardResult:
    cls = type(strategy)
    grid = strategy.grid() if optimize else [strategy.params]
    ppy = PERIODS_PER_YEAR[timeframe]
    warm = max(cls(**{**strategy.params, **g}).warmup() for g in grid)
    folds: list[Fold] = []
    trials = 0
    trial_sharpes: list[list[float]] = []  # per fold: annualised in-sample Sharpe of each variation (deflated Sharpe)
    start = 0
    while start + train_bars + test_bars <= len(bars):
        tr = bars.iloc[start:start + train_bars]
        te_start_i = start + train_bars
        te_end_i = te_start_i + test_bars
        best, best_s = None, -np.inf
        trial_sharpes.append([])
        for g in grid:
            params = {**strategy.params, **g}
            res = _eval(cls, params, tr, costs, ep, signal_filter=signal_filter)
            trials += 1
            if len(res.trades) < min_is_trades:
                continue
            s = _sharpe(res.equity.pct_change().dropna().to_numpy(), ppy)
            trial_sharpes[-1].append(s)
            if s > best_s:
                best, best_s = params, s
        if best is None:
            best, best_s = dict(strategy.params), float("nan")
        ctx = bars.iloc[max(0, te_start_i - warm):te_end_i]
        oos = _eval(cls, best, ctx, costs, ep, start=bars.index[te_start_i], signal_filter=signal_filter)
        folds.append(Fold(tr.index[0], tr.index[-1], bars.index[te_start_i], bars.index[te_end_i - 1], best, best_s, oos))
        start += test_bars

    # Chain fold returns into one OOS equity curve.
    rets = [f.oos.equity.pct_change().fillna(0.0) for f in folds]
    if rets:
        r = pd.concat(rets)
        oos_eq = ep.initial_equity * (1 + r).cumprod()
    else:
        oos_eq = pd.Series(dtype=float)
    trades = [t for f in folds for t in f.oos.trades]
    return WalkForwardResult(strategy.name, folds, oos_eq, trades, trials,
                             {"n_configs": len(grid), "trial_sharpes": trial_sharpes, "ppy": ppy})


def oos_as_result(wf: WalkForwardResult, ep: EngineParams, costs: Costs) -> BacktestResult:
    """Wrap a walk-forward OOS curve so the standard metrics apply."""
    pos = pd.concat([f.oos.positions for f in wf.folds]) if wf.folds else pd.Series(dtype=float)
    return BacktestResult(wf.oos_equity, wf.oos_trades, pos, ep, costs)
