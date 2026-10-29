"""Strategy interface.

A strategy maps bars to a signal frame indexed like the bars:
  target    -1 / 0 / +1 desired direction after the close of bar t
  stop_dist stop-loss distance in price units (NaN = no stop)
  tp_dist   optional take-profit distance in price units
  size_frac optional notional as a fraction of equity (overrides risk sizing)
Signals at t may only use information available at the close of t; the
engine executes them at the open of t+1.
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import pandas as pd


class Strategy(ABC):
    name: ClassVar[str]
    family: ClassVar[str]
    defaults: ClassVar[dict[str, Any]] = {}
    param_grid: ClassVar[dict[str, list]] = {}
    requires_stop: ClassVar[bool] = True

    def __init__(self, **params):
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {unknown}")
        self.params = {**self.defaults, **params}

    @abstractmethod
    def signals(self, bars: pd.DataFrame) -> pd.DataFrame: ...

    def warmup(self) -> int:
        """Bars needed before signals are meaningful."""
        return max([v for v in self.params.values() if isinstance(v, int)] + [20]) + 5

    def grid(self) -> list[dict[str, Any]]:
        keys = list(self.param_grid)
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*self.param_grid.values())]
        return [c for c in combos if self.valid(c)] or [dict(self.params)]

    def valid(self, params: dict[str, Any]) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in self.params.items())})"


def frame(index, target, stop_dist=None, tp_dist=None, size_frac=None) -> pd.DataFrame:
    out = pd.DataFrame({"target": target}, index=index)
    out["target"] = out["target"].fillna(0).astype(int).clip(-1, 1)
    out["stop_dist"] = stop_dist if stop_dist is not None else float("nan")
    out["tp_dist"] = tp_dist if tp_dist is not None else float("nan")
    out["size_frac"] = size_frac if size_frac is not None else float("nan")
    return out
