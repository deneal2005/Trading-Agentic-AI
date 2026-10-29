from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from tradeintel.config import load_settings
from tradeintel.data.providers import SyntheticProvider


# Most tests were written for EURUSD; pin it so they don't depend on the default market (SPY).
FX = {"market": {"asset_class": "forex", "instrument": "EURUSD", "pip_size": 0.0001},
      "costs": {"spread_pips": 1.2, "slippage_pips": 0.5, "financing_rate_annual": 0.01},
      "risk": {"max_leverage": 5.0},
      "execution": {"broker": "paper"}}  # never the broker from a developer's config/local.yaml


@pytest.fixture
def settings(tmp_path):
    return load_settings(env={}, root=tmp_path, overrides={**FX, "backtest": {"walkforward": {"train_bars": 300, "test_bars": 100}}})


@pytest.fixture
def synth_bars():
    b = SyntheticProvider(seed=3).fetch("EURUSD", "1d", "2015-01-01", n=1200)
    b.attrs["source"] = "SYNTHETIC"
    return b


def make_bars(closes, opens=None, highs=None, lows=None, start="2024-01-01", freq="D"):
    closes = np.asarray(closes, float)
    opens = np.asarray(opens if opens is not None else closes, float)
    highs = np.asarray(highs if highs is not None else np.maximum(opens, closes), float)
    lows = np.asarray(lows if lows is not None else np.minimum(opens, closes), float)
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": np.nan}, index=idx)


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
