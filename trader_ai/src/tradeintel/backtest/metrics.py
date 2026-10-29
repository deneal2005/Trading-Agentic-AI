"""Performance metrics computed from an equity curve and trade list."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .engine import BacktestResult

PERIODS_PER_YEAR = {"1d": 252, "4h": 252 * 6, "1h": 252 * 24}


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return max(0.0, float(-((equity - peak) / peak).min()))


def _sharpe(r: np.ndarray, ppy: int) -> float:
    if len(r) < 2 or np.std(r, ddof=1) == 0:
        return 0.0
    return float(np.mean(r) / np.std(r, ddof=1) * math.sqrt(ppy))


def bootstrap_sharpe_ci(r: np.ndarray, ppy: int, n: int = 1000, block: int = 20, seed: int = 0) -> tuple[float, float]:
    """Stationary-ish block bootstrap 90% CI for the annualised Sharpe."""
    if len(r) < block * 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(r) / block))
    stats = []
    for _ in range(n):
        starts = rng.integers(0, len(r) - block, nb)
        sample = np.concatenate([r[s:s + block] for s in starts])[: len(r)]
        stats.append(_sharpe(sample, ppy))
    return (float(np.percentile(stats, 5)), float(np.percentile(stats, 95)))


def compute_metrics(res: BacktestResult, timeframe: str = "1d") -> dict:
    eq = res.equity
    ppy = PERIODS_PER_YEAR[timeframe]
    r = eq.pct_change().dropna().to_numpy()
    start, end = float(eq.iloc[0]), float(eq.iloc[-1])
    years = max(len(eq) / ppy, 1e-9)
    neg = r[r < 0]
    downside = np.sqrt(np.mean(np.minimum(r, 0) ** 2)) if len(r) else 0.0
    pnl = np.array([t.pnl for t in res.trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    rmult = np.array([t.r_multiple for t in res.trades if not math.isnan(t.r_multiple)])
    exposure = float((res.positions != 0).mean()) if len(res.positions) else 0.0
    turnover_units = float(np.abs(np.diff(res.positions.to_numpy(), prepend=0)).sum())
    lo, hi = bootstrap_sharpe_ci(r, ppy)
    exits: dict[str, int] = {}
    for t in res.trades:
        exits[t.exit_reason or "open"] = exits.get(t.exit_reason or "open", 0) + 1
    return {
        "start_equity": start,
        "end_equity": end,
        "net_return": end / start - 1 if start else 0.0,
        "cagr": (end / start) ** (1 / years) - 1 if start > 0 and end > 0 else -1.0,
        "max_drawdown": max_drawdown(eq),
        "sharpe": _sharpe(r, ppy),
        "sharpe_ci90": [lo, hi],
        "sortino": float(np.mean(r) / downside * math.sqrt(ppy)) if downside > 0 else 0.0,
        "volatility_annual": float(np.std(r, ddof=1) * math.sqrt(ppy)) if len(r) > 1 else 0.0,
        "n_trades": int(len(pnl)),
        "win_rate": float(len(wins) / len(pnl)) if len(pnl) else 0.0,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else (float("inf") if len(wins) else 0.0),
        "expectancy": float(pnl.mean()) if len(pnl) else 0.0,
        "expectancy_r": float(rmult.mean()) if len(rmult) else float("nan"),
        "exposure": exposure,
        "turnover_units": turnover_units,
        "total_costs": float(sum(t.commissions + t.friction_cost + t.financing for t in res.trades)),
        "tail_loss_5pct": float(np.mean(np.sort(r)[: max(1, len(r) // 20)])) if len(r) else 0.0,
        "worst_period": float(neg.min()) if len(neg) else 0.0,
        "exit_reasons": exits,
        "bars": int(len(eq)),
        "years": years,
    }


def regime_labels(bars: pd.DataFrame, vol_n: int = 20, trend_n: int = 100) -> pd.Series:
    """Causal market-regime labels: trend (up/down/flat) x volatility (high/low).

    Vol threshold is the expanding median of realised vol, so no future
    information is used.
    """
    lr = np.log(bars["close"]).diff()
    vol = lr.rolling(vol_n, min_periods=vol_n).std()
    vol_med = vol.expanding(min_periods=vol_n * 3).median()
    ma = bars["close"].rolling(trend_n, min_periods=trend_n).mean()
    slope = ma.diff(20) / ma
    trend = np.where(slope > 0.01, "up", np.where(slope < -0.01, "down", "flat"))
    v = np.where(vol > vol_med, "highvol", "lowvol")
    lab = pd.Series([f"{a}_{b}" for a, b in zip(trend, v)], index=bars.index)
    return lab.where(vol_med.notna() & ma.notna(), "warmup")


def metrics_by_regime(res: BacktestResult, regimes: pd.Series, timeframe: str = "1d") -> dict[str, dict]:
    r = res.equity.pct_change().dropna()
    lab = regimes.reindex(r.index)
    ppy = PERIODS_PER_YEAR[timeframe]
    out = {}
    for name, grp in r.groupby(lab):
        out[str(name)] = {"bars": int(len(grp)), "mean_return": float(grp.mean()),
                          "sharpe": _sharpe(grp.to_numpy(), ppy)}
    return out
