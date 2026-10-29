"""Market-data providers.

Every provider returns UTC-indexed OHLCV and a `source` label. Synthetic data
is always labelled SYNTHETIC and must never be confused with market data.
"""

from __future__ import annotations

import io
import logging
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

YAHOO_SYMBOLS = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "AUDUSD": "AUDUSD=X"}
YAHOO_INTERVAL = {"1h": "60m", "1d": "1d"}


class DataUnavailable(RuntimeError):
    pass


class Provider(ABC):
    source: str = "unknown"

    @abstractmethod
    def fetch(self, instrument: str, timeframe: str, start: str, end: str | None = None) -> pd.DataFrame: ...


class YahooProvider(Provider):
    """Yahoo Finance chart API (unofficial, free, may lag or change)."""

    source = "yahoo"
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

    def __init__(self, timeout: int = 20, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self, instrument, timeframe, start, end=None):
        if timeframe not in YAHOO_INTERVAL:
            raise DataUnavailable(f"yahoo does not serve timeframe {timeframe}")
        sym = YAHOO_SYMBOLS.get(instrument, instrument)
        p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
        p2 = int((pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")).timestamp())
        try:
            r = self.session.get(
                self.URL.format(sym=sym),
                params={"period1": p1, "period2": p2, "interval": YAHOO_INTERVAL[timeframe]},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
        except Exception as e:  # network, HTTP, schema
            raise DataUnavailable(f"yahoo fetch failed for {sym}: {e}") from e
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame(
            {k: q.get(k) for k in ["open", "high", "low", "close", "volume"]},
            index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
        )
        if timeframe == "1d":
            df.index = df.index.normalize()
        return df


class StooqProvider(Provider):
    """Stooq daily CSV (free, no key)."""

    source = "stooq"
    URL = "https://stooq.com/q/d/l/"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def fetch(self, instrument, timeframe, start, end=None):
        if timeframe != "1d":
            raise DataUnavailable("stooq provider supports daily bars only")
        try:
            r = requests.get(self.URL, params={"s": instrument.lower(), "i": "d"}, timeout=self.timeout)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
        except Exception as e:
            raise DataUnavailable(f"stooq fetch failed: {e}") from e
        if "Date" not in df.columns:
            raise DataUnavailable(f"stooq returned unexpected payload: {r.text[:100]!r}")
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.pop("date"), utc=True)
        return df[df.index >= pd.Timestamp(start, tz="UTC")]


class CSVProvider(Provider):
    """Local CSV with a timestamp column and open/high/low/close[/volume]."""

    source = "csv"

    def __init__(self, path: str):
        self.path = path

    def fetch(self, instrument, timeframe, start, end=None):
        df = pd.read_csv(self.path)
        df.columns = [c.lower().strip() for c in df.columns]
        ts_col = next((c for c in ("timestamp", "datetime", "date", "time") if c in df.columns), None)
        if ts_col is None:
            raise DataUnavailable("CSV needs a timestamp/datetime/date/time column")
        df.index = pd.to_datetime(df.pop(ts_col), utc=True)
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df


class SyntheticProvider(Provider):
    """Regime-switching random walk for tests and offline development.

    Contains no market information. Any result computed on it says nothing
    about real-market performance.
    """

    source = "SYNTHETIC"

    def __init__(self, seed: int = 7, start_price: float = 1.10):
        self.seed = seed
        self.start_price = start_price

    def fetch(self, instrument, timeframe, start, end=None, n: int | None = None):
        rng = np.random.default_rng(self.seed)
        freq = {"1h": "h", "4h": "4h", "1d": "B"}[timeframe]
        if n is None:
            idx = pd.date_range(start, end or "2025-12-31", freq=freq, tz="UTC")
        else:
            idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
        m = len(idx)
        scale = {"1h": 0.0012, "4h": 0.0025, "1d": 0.006}[timeframe]
        regime = np.zeros(m, dtype=int)
        for i in range(1, m):
            regime[i] = regime[i - 1] if rng.random() > 0.01 else rng.integers(0, 3)
        drift = np.array([0.0, 0.0004, -0.0004])[regime] * (scale / 0.006)
        vol = np.array([1.0, 0.8, 1.4])[regime] * scale
        ret = drift + vol * rng.standard_t(5, size=m) / np.sqrt(5 / 3)
        close = self.start_price * np.exp(np.cumsum(ret))
        open_ = np.concatenate([[self.start_price], close[:-1]]) * np.exp(rng.normal(0, scale * 0.05, m))
        hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, scale * 0.4, m)))
        lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, scale * 0.4, m)))
        return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": np.nan}, index=idx)


def make_provider(name: str, csv_path: str | None = None, timeout: int = 20) -> Provider:
    if name == "yahoo":
        return YahooProvider(timeout)
    if name == "stooq":
        return StooqProvider(timeout)
    if name == "csv":
        if not csv_path:
            raise ValueError("data.csv_path is required for the csv provider")
        return CSVProvider(csv_path)
    if name == "synthetic":
        return SyntheticProvider()
    raise ValueError(f"unknown provider {name}")
