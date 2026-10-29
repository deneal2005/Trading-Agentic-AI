"""Transparent baseline strategies. Hypotheses and failure modes live in
knowledge/strategies/*.yaml; this file holds only the exact rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, frame
from .indicators import atr, donchian, rsi, sma


class NoTrade(Strategy):
    name = "no_trade"
    family = "baseline"
    requires_stop = False

    def signals(self, bars):
        return frame(bars.index, 0)

    def warmup(self):
        return 0


class BuyAndHold(Strategy):
    name = "buy_and_hold"
    family = "baseline"
    requires_stop = False
    defaults = {"notional_frac": 1.0}

    def signals(self, bars):
        return frame(bars.index, 1, size_frac=self.params["notional_frac"])

    def warmup(self):
        return 0


class SMACross(Strategy):
    """Trend following: long when fast SMA > slow SMA, short when below."""

    name = "sma_cross"
    family = "trend_following"
    defaults = {"fast": 20, "slow": 100, "atr_n": 14, "atr_mult": 3.0, "allow_short": True}
    param_grid = {"fast": [10, 20, 50], "slow": [50, 100, 200], "atr_mult": [2.0, 3.0]}

    def valid(self, p):
        return p["fast"] < p["slow"]

    def signals(self, bars):
        p = self.params
        f, s = sma(bars["close"], p["fast"]), sma(bars["close"], p["slow"])
        tgt = np.where(f > s, 1, np.where(f < s, -1 if p["allow_short"] else 0, 0))
        tgt = pd.Series(tgt, index=bars.index).where(s.notna(), 0)
        return frame(bars.index, tgt, stop_dist=p["atr_mult"] * atr(bars, p["atr_n"]))


class DonchianBreakout(Strategy):
    """Breakout: enter on close beyond the N-bar channel, exit on the
    opposite M-bar channel (M < N), turtle-style."""

    name = "donchian_breakout"
    family = "breakout"
    defaults = {"entry_n": 55, "exit_n": 20, "atr_n": 20, "atr_mult": 2.0, "allow_short": True}
    param_grid = {"entry_n": [20, 55, 100], "exit_n": [10, 20], "atr_mult": [2.0, 3.0]}

    def valid(self, p):
        return p["exit_n"] < p["entry_n"]

    def signals(self, bars):
        p = self.params
        up_e, dn_e = donchian(bars, p["entry_n"])
        up_x, dn_x = donchian(bars, p["exit_n"])
        c = bars["close"].to_numpy()
        tgt = np.zeros(len(bars), dtype=int)
        pos = 0
        for i in range(len(bars)):
            if np.isnan(up_e.iat[i]):
                continue
            if pos == 1 and c[i] < dn_x.iat[i]:
                pos = 0
            elif pos == -1 and c[i] > up_x.iat[i]:
                pos = 0
            if pos == 0:
                if c[i] > up_e.iat[i]:
                    pos = 1
                elif c[i] < dn_e.iat[i] and p["allow_short"]:
                    pos = -1
            tgt[i] = pos
        return frame(bars.index, tgt, stop_dist=p["atr_mult"] * atr(bars, p["atr_n"]))


class RSIMeanReversion(Strategy):
    """Mean reversion: buy oversold / sell overbought RSI, exit when RSI
    returns to the middle, or after max_hold bars. Thresholds are
    symmetric: short above 100 - lower."""

    name = "rsi_mean_reversion"
    family = "mean_reversion"
    defaults = {"period": 14, "lower": 30, "exit_level": 50, "max_hold": 10,
                "atr_n": 14, "atr_mult": 2.0, "allow_short": True}
    param_grid = {"period": [2, 14], "lower": [10, 30], "max_hold": [5, 10], "atr_mult": [1.5, 2.5]}

    def signals(self, bars):
        p = self.params
        upper = 100 - p["lower"]  # symmetric thresholds
        r = rsi(bars["close"], p["period"]).to_numpy()
        tgt = np.zeros(len(bars), dtype=int)
        pos, held = 0, 0
        for i in range(len(bars)):
            if np.isnan(r[i]):
                continue
            if pos != 0:
                held += 1
                if (pos == 1 and r[i] >= p["exit_level"]) or (pos == -1 and r[i] <= p["exit_level"]) or held >= p["max_hold"]:
                    pos, held = 0, 0
            if pos == 0:
                if r[i] < p["lower"]:
                    pos, held = 1, 0
                elif r[i] > upper and p["allow_short"]:
                    pos, held = -1, 0
            tgt[i] = pos
        return frame(bars.index, tgt, stop_dist=p["atr_mult"] * atr(bars, p["atr_n"]))


REGISTRY: dict[str, type[Strategy]] = {
    c.name: c for c in [NoTrade, BuyAndHold, SMACross, DonchianBreakout, RSIMeanReversion]
}


def build(name: str, **params) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](**params)
