"""End-to-end research run: walk-forward a strategy, compare it with
baselines on the same out-of-sample windows, stress costs, break results
down by regime, apply the promotion gate and record everything."""

from __future__ import annotations

import logging

import pandas as pd

from ..config import Settings
from ..data.store import frame_hash
from ..experiments.promotion import evaluate_promotion
from ..experiments.registry import Registry
from ..strategies.library import build
from .engine import EngineParams
from .metrics import compute_metrics, metrics_by_regime, regime_labels
from .walkforward import oos_as_result, split_holdout, walk_forward

log = logging.getLogger(__name__)


def engine_params(s: Settings) -> EngineParams:
    m = s.market
    account_is = "quote" if m.quote_ccy == m.account_currency else "base" if m.base_ccy == m.account_currency else None
    if account_is is None:
        raise ValueError(f"account currency {m.account_currency} is neither side of {m.instrument}; cross conversion not implemented")
    return EngineParams(
        initial_equity=s.backtest.initial_equity,
        risk_per_trade=s.risk.max_risk_per_trade,
        max_leverage=s.risk.max_leverage,
        max_units=s.risk.max_position_units,
        pip_size=m.pip_size,
        account_is=account_is,
    )


def research(s: Settings, bars: pd.DataFrame, strategy_name: str, params: dict | None = None,
             registry: Registry | None = None, optimize: bool = True) -> dict:
    source = bars.attrs.get("source", "unknown")
    synthetic = source == "SYNTHETIC"
    research_bars, holdout = split_holdout(bars, s.backtest.holdout_fraction)
    ep = engine_params(s)
    wfc = s.backtest.walkforward
    tf = s.market.timeframe

    strat = build(strategy_name, **(params or {}))
    wf = walk_forward(strat, research_bars, s.costs, ep, wfc.train_bars, wfc.test_bars, tf, optimize=optimize)
    if not wf.folds:
        raise ValueError(f"not enough data for walk-forward: {len(research_bars)} bars < {wfc.train_bars + wfc.test_bars}")
    oos = compute_metrics(oos_as_result(wf, ep, s.costs), tf)

    k = s.promotion.max_cost_stress_multiplier_loss
    wf_stress = walk_forward(strat, research_bars, s.costs.scaled(k), ep, wfc.train_bars, wfc.test_bars, tf, optimize=optimize)
    stressed = compute_metrics(oos_as_result(wf_stress, ep, s.costs.scaled(k)), tf)

    baselines = {}
    for b in ("no_trade", "buy_and_hold"):
        bwf = walk_forward(build(b), research_bars, s.costs, ep, wfc.train_bars, wfc.test_bars, tf, optimize=False)
        baselines[b] = compute_metrics(oos_as_result(bwf, ep, s.costs), tf)

    by_regime = metrics_by_regime(oos_as_result(wf, ep, s.costs), regime_labels(research_bars), tf)
    gate = evaluate_promotion(oos, baselines, stressed, s.promotion, synthetic)

    report = {
        "strategy": strategy_name,
        "instrument": s.market.instrument,
        "timeframe": tf,
        "data_source": source,
        "synthetic": synthetic,
        "research_period": [str(research_bars.index[0]), str(research_bars.index[-1])],
        "holdout_period": [str(holdout.index[0]), str(holdout.index[-1])] if len(holdout) else None,
        "n_folds": len(wf.folds),
        "n_trials": wf.n_trials,
        "folds": [{"test_start": str(f.test_start), "test_end": str(f.test_end), "params": f.chosen_params,
                   "is_sharpe": f.is_sharpe, "oos_return": float(f.oos.equity.iloc[-1] / f.oos.equity.iloc[0] - 1)}
                  for f in wf.folds],
        "oos": oos,
        "cost_stress": {"multiplier": k, **stressed},
        "baselines": baselines,
        "by_regime": by_regime,
        "gate": {"passed": gate.passed, "reasons": gate.reasons, "checks": gate.checks},
        "oos_equity": wf.oos_equity,
    }
    if registry is not None:
        report["experiment_id"] = registry.record(
            "walkforward", strategy=strategy_name, params=params or strat.params, instrument=s.market.instrument,
            timeframe=tf, data_source=source, data_hash=frame_hash(bars), config=s.model_dump(mode="json"),
            n_trials=wf.n_trials + wf_stress.n_trials, metrics={k2: v for k2, v in report.items() if k2 != "oos_equity"},
            verdict="promotable" if gate.passed else "rejected",
        )
    log.info("research complete", extra={"data": {"strategy": strategy_name, "passed": gate.passed, "sharpe": oos["sharpe"]}})
    return report


def holdout_test(s: Settings, bars: pd.DataFrame, strategy_name: str, params: dict, registry: Registry,
                 max_uses: int = 1) -> dict:
    """Single evaluation of fixed params on the untouched holdout. Each use is
    recorded; repeated use is refused because it turns the holdout into
    another tuning set."""
    from .engine import run_backtest

    h = frame_hash(bars)
    used = registry.holdout_uses(strategy_name, h)
    if used >= max_uses:
        raise PermissionError(f"holdout already used {used}x for {strategy_name} on this dataset; collect new data instead")
    research_bars, holdout = split_holdout(bars, s.backtest.holdout_fraction)
    strat = build(strategy_name, **params)
    warm = strat.warmup()
    ctx = pd.concat([research_bars.iloc[-warm:], holdout])
    ep = engine_params(s)
    ep.require_stop = strat.requires_stop
    res = run_backtest(ctx, strat.signals(ctx), s.costs, ep, trade_start=holdout.index[0])
    m = compute_metrics(res, s.market.timeframe)
    # the holdout must confirm the walk-forward: profitable, positive Sharpe, drawdown within the promotion limit
    passed = m["net_return"] > 0 and m["sharpe"] > 0 and m["max_drawdown"] <= s.promotion.max_oos_drawdown
    m["verdict"] = "passed" if passed else "failed"
    registry.record("holdout", strategy=strategy_name, params=strat.params, instrument=s.market.instrument,
                    timeframe=s.market.timeframe, data_source=bars.attrs.get("source", "unknown"), data_hash=h,
                    config=s.model_dump(mode="json"), n_trials=1, metrics=m, verdict=m["verdict"])
    return m
