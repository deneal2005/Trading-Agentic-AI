import numpy as np
import pandas as pd
import pytest

from tradeintel.backtest.engine import EngineParams, run_backtest
from tradeintel.backtest.metrics import compute_metrics, max_drawdown
from tradeintel.config import Costs
from tradeintel.strategies.base import frame
from conftest import make_bars

NOCOST = Costs(spread_pips=0, slippage_pips=0, commission_per_million=0, financing_rate_annual=0)
SPREAD2 = Costs(spread_pips=2, slippage_pips=0, commission_per_million=0, financing_rate_annual=0)


def sig(bars, target, stop=None, size=None):
    return frame(bars.index, pd.Series(target, index=bars.index), stop_dist=stop, size_frac=size)


def test_next_bar_fill_and_spread_cost():
    b = make_bars(closes=[1.0, 1.05, 1.1, 1.1], opens=[1.0, 1.0, 1.1, 1.1])
    res = run_backtest(b, sig(b, [1, 1, 0, 0], size=1.0), SPREAD2, EngineParams(require_stop=False))
    t = res.trades[0]
    assert t.entry_time == b.index[1] and t.entry_price == pytest.approx(1.0001)
    assert t.exit_time == b.index[3] and t.exit_price == pytest.approx(1.0999)
    assert t.units == 10000
    assert t.pnl == pytest.approx(998.0)
    assert res.equity.iloc[-1] == pytest.approx(10998.0)


def test_no_signal_no_trades_equity_flat():
    b = make_bars(np.linspace(1, 2, 50))
    res = run_backtest(b, sig(b, [0] * 50), SPREAD2)
    assert res.trades == [] and (res.equity == 10000).all()


def test_risk_based_sizing():
    b = make_bars(closes=[1.0, 1.0, 1.0], opens=[1.0, 1.0, 1.0])
    res = run_backtest(b, sig(b, [1, 1, 1], stop=[0.05] * 3), SPREAD2, EngineParams(risk_per_trade=0.005))
    # units = 10000 * 0.005 / (0.05 + 2 * 0.0001)
    assert res.trades[0].units == 996


def test_stop_fill_at_stop_and_no_reentry():
    b = make_bars(closes=[1.0, 1.0, 0.97, 1.0, 1.0], opens=[1.0, 1.0, 0.99, 0.97, 1.0],
                  lows=[1.0, 1.0, 0.94, 0.97, 1.0], highs=[1.0, 1.0, 0.99, 1.0, 1.0])
    res = run_backtest(b, sig(b, [1] * 5, stop=[0.05] * 5), SPREAD2)
    assert len(res.trades) == 1, "must not re-enter the same direction after a stop"
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(1.0001 - 0.05 - 0.0001)
    assert t.r_multiple == pytest.approx(-1.0, abs=0.01)


def test_gap_through_stop_fills_at_open():
    b = make_bars(closes=[1.0, 1.0, 0.92], opens=[1.0, 1.0, 0.93], lows=[1.0, 1.0, 0.90], highs=[1.0, 1.0, 0.93])
    res = run_backtest(b, sig(b, [1, 1, 1], stop=[0.05] * 3), SPREAD2)
    assert res.trades[0].exit_price == pytest.approx(0.93 - 0.0001)


def test_financing_charged_per_day():
    b = make_bars(closes=[1.0] * 4, opens=[1.0] * 4)
    c = Costs(spread_pips=0, slippage_pips=0, commission_per_million=0, financing_rate_annual=0.0365)
    res = run_backtest(b, sig(b, [1, 1, 1, 0], size=1.0), c, EngineParams(require_stop=False))
    # held from bar1 to bar3: financing for 2 days on 10,000 notional at 0.01%/day
    assert res.trades[0].financing == pytest.approx(2 * 10000 * 0.0001)
    assert res.equity.iloc[-1] == pytest.approx(10000 - 2.0)


def test_short_trade_pnl():
    b = make_bars(closes=[1.0, 0.95, 0.9, 0.9], opens=[1.0, 1.0, 0.9, 0.9])
    res = run_backtest(b, sig(b, [-1, -1, 0, 0], size=1.0), NOCOST, EngineParams(require_stop=False))
    assert res.trades[0].pnl == pytest.approx(0.1 * 10000)


def test_leverage_cap():
    b = make_bars(closes=[1.0] * 3)
    res = run_backtest(b, sig(b, [1, 1, 1], size=50.0), NOCOST, EngineParams(max_leverage=5, require_stop=False))
    assert res.trades[0].units == 50000


def test_trade_start_blocks_earlier_trades():
    b = make_bars(closes=[1.0] * 10)
    res = run_backtest(b, sig(b, [1] * 10, size=1.0), NOCOST, EngineParams(require_stop=False), trade_start=b.index[5])
    assert res.trades[0].entry_time >= b.index[5]
    assert res.equity.index[0] == b.index[5]


def test_mismatched_index_rejected():
    b = make_bars([1.0] * 5)
    with pytest.raises(ValueError):
        run_backtest(b, sig(b, [0] * 5).iloc[1:], NOCOST)


def test_metrics_known_values():
    s = pd.Series([100, 120, 90, 110], index=pd.date_range("2024", periods=4, tz="UTC"))
    assert max_drawdown(s) == pytest.approx(0.25)
    px = [1.0, 1.0, 1.1, 1.1, 1.0, 0.9, 0.9]
    b = make_bars(closes=px, opens=px)
    res = run_backtest(b, sig(b, [1, 1, 0, 1, 1, 0, 0], size=1.0), NOCOST, EngineParams(require_stop=False))
    m = compute_metrics(res)
    # +1000 on 10,000 units, then 11,000 units bought at 1.0 and sold at 0.9 = -1100
    assert m["n_trades"] == 2 and m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(1000 / 1100)
