"""OHLCV validation. Problems are reported, never silently patched."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

REQUIRED = ["open", "high", "low", "close"]

TIMEFRAME_DELTA = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}


@dataclass
class ValidationReport:
    rows_in: int
    rows_out: int
    issues: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rows_out > 0 and not any(i.startswith("FATAL") for i in self.issues)


def drop_unfinished(df: pd.DataFrame, timeframe: str, asset_class: str, now=None) -> tuple[pd.DataFrame, int]:
    """Drop bars whose period hasn't ended yet. Vendors return today's candle while the session is still running;
    its 'close' is just the latest price, which backtests (built on finished bars) never see."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if timeframe == "1d" and asset_class == "equity":
        end = df.index + pd.Timedelta(hours=21)  # US close 16:00 New York = 20:00 or 21:00 UTC; 21:00 is safe in both
    else:
        end = df.index + TIMEFRAME_DELTA[timeframe]
    keep = end <= now
    return df[keep], int((~keep).sum())


def validate_bars(df: pd.DataFrame, timeframe: str = "1d") -> tuple[pd.DataFrame, ValidationReport]:
    """Return a cleaned copy and a report.

    Index must be a tz-aware UTC DatetimeIndex of bar *open* times. Rows that
    are internally inconsistent are dropped (and counted); gaps are reported.
    """
    rep = ValidationReport(rows_in=len(df), rows_out=0)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        rep.issues.append(f"FATAL missing columns: {missing}")
        return df.iloc[0:0], rep
    if not isinstance(df.index, pd.DatetimeIndex):
        rep.issues.append("FATAL index is not a DatetimeIndex")
        return df.iloc[0:0], rep

    out = df.copy()
    if out.index.tz is None:
        rep.issues.append("index was tz-naive; assumed UTC")
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")

    def drop(mask: pd.Series, reason: str) -> None:
        nonlocal out
        n = int(mask.sum())
        if n:
            rep.dropped[reason] = rep.dropped.get(reason, 0) + n
            out = out[~mask]

    dup = out.index.duplicated(keep="last")
    drop(pd.Series(dup, index=out.index), "duplicate_timestamp")
    out = out.sort_index()
    drop(out[REQUIRED].isna().any(axis=1), "nan_price")
    drop((out[REQUIRED] <= 0).any(axis=1), "non_positive_price")
    bad_hl = (out["high"] < out[["open", "close", "low"]].max(axis=1)) | (out["low"] > out[["open", "close", "high"]].min(axis=1))
    drop(bad_hl, "inconsistent_ohlc")
    # Single-bar jumps > 20 standard deviations are flagged, not dropped:
    # they may be real (e.g. SNB 2015) and must be checked by a human.
    r = np.log(out["close"]).diff()
    if len(r) > 30:
        z = (r - r.rolling(100, min_periods=30).mean()) / r.rolling(100, min_periods=30).std()
        spikes = out.index[(z.abs() > 20).fillna(False)]
        if len(spikes):
            rep.issues.append(f"extreme moves at {[str(t) for t in spikes[:5]]} (kept; verify)")
    if "volume" not in out.columns:
        out["volume"] = np.nan

    delta = TIMEFRAME_DELTA.get(timeframe)
    if delta is not None and len(out) > 2:
        gaps = out.index.to_series().diff()
        # Weekends are expected for FX/equities; flag gaps > 4 bars.
        big = gaps[gaps > 4 * delta] if timeframe != "1d" else gaps[gaps > pd.Timedelta(days=5)]
        if len(big):
            rep.issues.append(f"{len(big)} gaps larger than expected (first at {big.index[0]})")
    rep.rows_out = len(out)
    if rep.rows_out == 0:
        rep.issues.append("FATAL no valid rows")
    return out[REQUIRED + ["volume"]], rep


def is_stale(last_bar_time: pd.Timestamp, now: pd.Timestamp, timeframe: str, max_bars: int) -> bool:
    """True when the latest bar is older than max_bars timeframes (weekend-aware for daily).

    Bar timestamps are open times, so a bar only completes one timeframe
    after its stamp; the allowance is measured from that completion.
    """
    delta = TIMEFRAME_DELTA[timeframe]
    allowance = delta * (max_bars + 1)
    if timeframe == "1d":
        allowance += pd.Timedelta(days=2)  # weekend
    return (now - last_bar_time) > allowance
