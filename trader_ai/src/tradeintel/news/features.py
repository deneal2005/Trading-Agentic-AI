"""Point-in-time event features and the news ablation test.

Scheduled event *times* are public in advance, so using them at bar t is
not leakage. Event *outcomes* (actual values) may only be used for bars that
close after the release time.
"""

from __future__ import annotations

import pandas as pd

from ..backtest.engine import EngineParams
from ..backtest.metrics import compute_metrics
from ..backtest.walkforward import oos_as_result, walk_forward
from ..config import Costs
from ..strategies.base import Strategy


def events_frame(events: list[dict]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=["event_time", "currency", "impact", "title"])
    df = pd.DataFrame(events)
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True, format="ISO8601")
    return df[["event_time", "currency", "impact", "title"]]


def upcoming_event_flag(bars: pd.DataFrame, events: pd.DataFrame, currencies: set[str], impact: str = "high") -> pd.Series:
    """True at bar t if a matching event is scheduled between t's close and
    t+1's close (i.e. during the bar a new position would be held)."""
    ev = events[(events["impact"] == impact) & (events["currency"].isin(currencies))]["event_time"].sort_values()
    idx = bars.index
    if len(idx) < 2 or ev.empty:
        return pd.Series(False, index=idx)
    step = idx.to_series().diff().median()
    close_t = idx + step
    next_close = close_t.to_series(index=idx).shift(-1).fillna(close_t[-1] + step)
    flag = pd.Series(False, index=idx)
    for t, c0, c1 in zip(idx, close_t, next_close):
        lo, hi = ev.searchsorted(c0), ev.searchsorted(c1)
        flag[t] = hi > lo
    return flag


def blackout_filter(events: pd.DataFrame, currencies: set[str], mode: str = "no_entry"):
    """Signal filter: before a high-impact event, either block new entries
    (`no_entry`) or go flat (`flat`)."""

    def f(bars: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
        flag = upcoming_event_flag(bars, events, currencies)
        out = sig.copy()
        if mode == "flat":
            out.loc[flag, "target"] = 0
        else:
            prev = out["target"].shift(1).fillna(0)
            entering = flag & (out["target"] != 0) & (out["target"] != prev)
            out.loc[entering, "target"] = prev[entering].astype(int)
        return out

    return f


def news_ablation(strategy: Strategy, bars: pd.DataFrame, events: pd.DataFrame, currencies: set[str], costs: Costs,
                  ep: EngineParams, train_bars: int, test_bars: int, timeframe: str = "1d", mode: str = "no_entry") -> dict:
    """Same strategy, same data, same folds: with vs without the news filter."""
    base = walk_forward(strategy, bars, costs, ep, train_bars, test_bars, timeframe)
    filt = walk_forward(strategy, bars, costs, ep, train_bars, test_bars, timeframe,
                        signal_filter=blackout_filter(events, currencies, mode))
    mb = compute_metrics(oos_as_result(base, ep, costs), timeframe)
    mf = compute_metrics(oos_as_result(filt, ep, costs), timeframe)
    n_events = int(upcoming_event_flag(bars, events, currencies).sum())
    keys = ["net_return", "sharpe", "max_drawdown", "n_trades", "profit_factor"]
    return {
        "strategy": strategy.name, "mode": mode, "event_bars": n_events,
        "without_news": {k: mb[k] for k in keys}, "with_news": {k: mf[k] for k in keys},
        "delta": {k: mf[k] - mb[k] for k in keys if isinstance(mb[k], (int, float))},
        "caveat": "a difference is evidence only if it holds across folds and periods; small event counts are not conclusive",
    }
