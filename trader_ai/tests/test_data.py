import numpy as np
import pandas as pd

from tradeintel.data.store import BarStore
from tradeintel.data.validate import is_stale, validate_bars
from conftest import make_bars


def test_validation_drops_bad_rows():
    b = make_bars([1.0, 1.1, 1.2, 1.3, 1.4])
    b.iloc[1, b.columns.get_loc("high")] = 0.5      # high below close
    b.iloc[2, b.columns.get_loc("close")] = np.nan
    b.iloc[3, b.columns.get_loc("open")] = -1
    clean, rep = validate_bars(b)
    assert len(clean) == 2
    assert rep.dropped == {"inconsistent_ohlc": 1, "nan_price": 1, "non_positive_price": 1}


def test_validation_dedups_and_sorts_and_localizes():
    b = make_bars([1.0, 1.1, 1.2])
    b = pd.concat([b, b.iloc[[0]]]).iloc[::-1]
    b.index = b.index.tz_localize(None)
    clean, rep = validate_bars(b)
    assert clean.index.is_monotonic_increasing and clean.index.tz is not None
    assert rep.dropped["duplicate_timestamp"] == 1
    assert any("tz-naive" in i for i in rep.issues)


def test_missing_columns_fatal():
    clean, rep = validate_bars(pd.DataFrame({"close": [1.0]}, index=pd.date_range("2024", periods=1, tz="UTC")))
    assert not rep.ok


def test_staleness():
    t = pd.Timestamp("2024-01-05", tz="UTC")  # Friday
    assert not is_stale(t, pd.Timestamp("2024-01-08 12:00", tz="UTC"), "1d", 1)  # Monday: weekend allowed
    assert is_stale(t, pd.Timestamp("2024-01-12", tz="UTC"), "1d", 1)


def test_store_roundtrip(tmp_path, synth_bars):
    st = BarStore(tmp_path / "db.sqlite")
    st.upsert(synth_bars.iloc[:50], "EURUSD", "1d", "SYNTHETIC")
    st.upsert(synth_bars.iloc[40:60], "EURUSD", "1d", "SYNTHETIC")  # overlap is idempotent
    out = st.load("EURUSD", "1d")
    assert len(out) == 60 and out.attrs["source"] == "SYNTHETIC"
    np.testing.assert_allclose(out["close"].to_numpy(), synth_bars["close"].iloc[:60].to_numpy())
