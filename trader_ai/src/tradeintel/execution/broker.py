"""Broker interface. Implementations must never route to a live-money
account: `environment` is reported by the broker itself and verified
before any order is sent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class BrokerError(RuntimeError):
    pass


@dataclass
class Quote:
    instrument: str
    bid: float
    ask: float
    time: datetime


@dataclass
class AccountInfo:
    account_id: str
    environment: str        # paper | practice | live
    currency: str
    balance: float
    equity: float


@dataclass
class OrderResult:
    status: str             # filled | rejected | pending | error
    broker_order_id: str | None
    fill_price: float | None
    filled_units: float
    message: str = ""


@dataclass
class BrokerPosition:
    instrument: str
    units: float            # signed
    avg_price: float
    stop_price: float | None


class Broker(ABC):
    @abstractmethod
    def account(self) -> AccountInfo: ...

    @abstractmethod
    def quote(self, instrument: str) -> Quote: ...

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def submit_market(self, instrument: str, units: float, stop_price: float | None, take_profit: float | None,
                      client_id: str) -> OrderResult: ...

    @abstractmethod
    def close_all(self) -> list[OrderResult]: ...
