"""Bar-by-bar backtest engine.

Execution model (deliberately conservative):
- A signal computed at the close of bar t is filled at the open of bar t+1.
- Every fill pays half the spread plus slippage, against the trader.
- Stops are checked intrabar; a gap through the stop fills at the open.
  When both stop and take-profit are touched in the same bar, the stop is
  assumed to have filled first.
- Overnight financing is charged on notional for each calendar day held.
- Position size = equity * risk_per_trade / stop distance, capped by
  leverage and max units. Strategies may instead request a notional size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Costs


@dataclass
class EngineParams:
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.005
    max_leverage: float = 5.0
    max_units: float = 1e7
    pip_size: float = 0.0001
    require_stop: bool = True
    # How P&L in quote currency converts to account currency:
    # "quote" (quote ccy == account ccy) or "base" (base ccy == account ccy).
    account_is: str = "quote"


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None
    direction: int
    units: float
    entry_price: float
    exit_price: float | None = None
    stop_price: float | None = None
    tp_price: float | None = None
    exit_reason: str | None = None
    commissions: float = 0.0
    friction_cost: float = 0.0   # spread + slippage paid, account ccy (informational)
    financing: float = 0.0
    gross_pnl: float = 0.0       # includes friction via fill prices
    initial_risk: float = float("nan")

    @property
    def pnl(self) -> float:
        return self.gross_pnl - self.commissions - self.financing

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.initial_risk if self.initial_risk and self.initial_risk > 0 else float("nan")


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list[Trade]
    positions: pd.Series
    params: EngineParams
    costs: Costs
    meta: dict = field(default_factory=dict)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{**t.__dict__, "pnl": t.pnl, "r_multiple": t.r_multiple} for t in self.trades])


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    costs: Costs,
    params: EngineParams | None = None,
    trade_start: pd.Timestamp | None = None,
) -> BacktestResult:
    p = params or EngineParams()
    if not bars.index.equals(signals.index):
        raise ValueError("bars and signals must share the same index")
    if p.account_is not in ("quote", "base"):
        raise ValueError("account_is must be 'quote' or 'base'")
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = bars.index
    tgt = signals["target"].to_numpy(int)
    stop_d = signals["stop_dist"].to_numpy(float)
    tp_d = signals["tp_dist"].to_numpy(float)
    size_f = signals["size_frac"].to_numpy(float)
    if trade_start is not None:
        tgt = np.where(idx < trade_start, 0, tgt)

    friction = (costs.spread_pips / 2 + costs.slippage_pips) * p.pip_size

    def to_acct(x_quote: float, price: float) -> float:
        return x_quote if p.account_is == "quote" else x_quote / price

    def notional(units: float, price: float) -> float:
        return units * price if p.account_is == "quote" else units

    def units_for_notional(n_acct: float, price: float) -> float:
        return n_acct / price if p.account_is == "quote" else n_acct

    cash = p.initial_equity
    equity = np.empty(len(idx))
    pos_series = np.zeros(len(idx))
    trades: list[Trade] = []
    open_t: Trade | None = None
    # After a stop or take-profit, do not re-enter the same direction until
    # the strategy's target changes; otherwise a stopped trend signal would
    # re-enter on the very next bar.
    blocked_dir = 0

    def close_position(i: int, raw_price: float, reason: str) -> None:
        nonlocal cash, open_t
        t = open_t
        fill = raw_price - t.direction * friction
        comm = notional(t.units, fill) * costs.commission_per_million / 1e6
        t.exit_time, t.exit_price, t.exit_reason = idx[i], fill, reason
        t.gross_pnl = to_acct(t.direction * (fill - t.entry_price) * t.units, fill)
        t.commissions += comm
        t.friction_cost += to_acct(friction * t.units, fill)
        cash += t.gross_pnl - comm  # financing was booked bar by bar
        trades.append(t)
        open_t = None

    for i in range(len(idx)):
        # 1) act on the previous bar's signal at this bar's open
        if i > 0:
            want = int(tgt[i - 1])
            if blocked_dir and want != blocked_dir:
                blocked_dir = 0
            if open_t is not None and want != open_t.direction:
                close_position(i, o[i], "signal")
            if want != 0 and open_t is None and want != blocked_dir:
                sd, eq_now, px = stop_d[i - 1], cash, o[i]
                units = 0.0
                if not np.isnan(size_f[i - 1]):
                    units = units_for_notional(eq_now * size_f[i - 1], px)
                elif not np.isnan(sd) and sd > 0:
                    units = eq_now * p.risk_per_trade / to_acct(sd + 2 * friction, px)
                elif not p.require_stop:
                    units = units_for_notional(eq_now, px)
                units = float(np.floor(min(units, units_for_notional(p.max_leverage * eq_now, px), p.max_units)))
                if units > 0:
                    fill = px + want * friction
                    comm = notional(units, fill) * costs.commission_per_million / 1e6
                    has_stop = not np.isnan(sd) and sd > 0
                    open_t = Trade(
                        idx[i], None, want, units, fill,
                        stop_price=fill - want * sd if has_stop else None,
                        tp_price=fill + want * tp_d[i - 1] if not np.isnan(tp_d[i - 1]) else None,
                        commissions=comm,
                        friction_cost=to_acct(friction * units, fill),
                        initial_risk=to_acct((sd + 2 * friction) * units, fill) if has_stop else float("nan"),
                    )
                    cash -= comm

        # 2) intrabar stop / take-profit. On the entry bar the open is the
        # fill, so a gap cannot have jumped the stop.
        if open_t is not None:
            d, sp, tp = open_t.direction, open_t.stop_price, open_t.tp_price
            entry_bar = open_t.entry_time == idx[i]
            if sp is not None and ((d == 1 and l[i] <= sp) or (d == -1 and h[i] >= sp)):
                gapped = not entry_bar and ((d == 1 and o[i] < sp) or (d == -1 and o[i] > sp))
                blocked_dir = d
                close_position(i, o[i] if gapped else sp, "stop")
            elif tp is not None and ((d == 1 and h[i] >= tp) or (d == -1 and l[i] <= tp)):
                gapped = not entry_bar and ((d == 1 and o[i] > tp) or (d == -1 and o[i] < tp))
                blocked_dir = d
                close_position(i, o[i] if gapped else tp, "take_profit")

        # 3) book financing for holding to the next bar, then mark to market
        if open_t is not None:
            if i + 1 < len(idx):
                days = max((idx[i + 1] - idx[i]).total_seconds() / 86400.0, 0.0)
                fin = notional(open_t.units, c[i]) * costs.financing_rate_annual * days / 365.0
                open_t.financing += fin
                cash -= fin
            exit_px = c[i] - open_t.direction * friction
            equity[i] = cash + to_acct(open_t.direction * (exit_px - open_t.entry_price) * open_t.units, c[i])
            pos_series[i] = open_t.direction * open_t.units
        else:
            equity[i] = cash

    if open_t is not None:  # close at the final close for accounting
        close_position(len(idx) - 1, c[-1], "end_of_data")
        equity[-1] = cash

    eq = pd.Series(equity, index=idx, name="equity")
    pos = pd.Series(pos_series, index=idx, name="position")
    if trade_start is not None:
        keep = idx >= trade_start
        eq, pos = eq[keep], pos[keep]
    return BacktestResult(eq, trades, pos, p, costs)
