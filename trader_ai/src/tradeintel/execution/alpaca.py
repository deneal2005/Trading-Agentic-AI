"""Alpaca trading API client restricted to PAPER trading.

The base URL is fixed to the paper host, and the account number must carry
Alpaca's paper prefix "PA"; anything else is refused. This code has not yet
been exercised against a real paper account: run
`tradeintel broker-check --broker alpaca` (read-only) first.
"""

from __future__ import annotations

import os
import time

import requests

from .broker import AccountInfo, Broker, BrokerError, BrokerPosition, OrderResult, Quote
from .oanda import parse_oanda_time

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class AlpacaPaperBroker(Broker):
    def __init__(self, key_id: str | None = None, secret: str | None = None, timeout: int = 15,
                 session: requests.Session | None = None, feed: str = "iex", quote_samples: int = 3,
                 quote_pause: float = 1.0):
        key_id = key_id or os.environ.get("APCA_API_KEY_ID", "")
        secret = secret or os.environ.get("APCA_API_SECRET_KEY", "")
        if not key_id or not secret:
            raise BrokerError("APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set in trader_ai/.env (see .env.example)")
        self.base = PAPER_URL
        assert "paper-api" in self.base
        self.timeout = timeout
        self.feed = feed
        self.quote_samples, self.quote_pause = quote_samples, quote_pause
        self.s = session or requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret})

    def _req(self, method: str, url: str, **kw):
        try:
            r = self.s.request(method, url, timeout=self.timeout, **kw)
            r.raise_for_status()
            return r.json() if r.content else None
        except (requests.RequestException, ValueError) as e:
            raise BrokerError(f"Alpaca {method} {url.split('.markets', 1)[-1]} failed: {e}") from e

    def account(self):
        a = self._req("GET", f"{self.base}/v2/account")
        num = str(a.get("account_number", ""))
        if not num.startswith("PA"):
            raise BrokerError("Alpaca account number lacks the paper 'PA' prefix; refusing (paper trading only)")
        return AccountInfo(num, "practice", a["currency"], float(a["cash"]), float(a["equity"]))

    def quote(self, instrument):
        # One IEX snapshot is noisy (seen: 3c, 73c, 8c within 4 s on SPY). The market-wide spread is never wider
        # than any one exchange's, so the tightest of a few IEX snapshots is still an upper bound on the true spread.
        best = None
        for i in range(self.quote_samples):
            if i:
                time.sleep(self.quote_pause)
            q = self._req("GET", f"{DATA_URL}/v2/stocks/{instrument}/quotes/latest", params={"feed": self.feed})["quote"]
            bid, ask = float(q["bp"]), float(q["ap"])
            if bid > 0 and ask >= bid and (best is None or ask - bid < best.ask - best.bid):
                best = Quote(instrument, bid, ask, parse_oanda_time(q["t"]))
        if best is None:
            raise BrokerError(f"no valid {instrument} quote from Alpaca ({self.feed} feed)")
        return best

    def clock(self) -> dict:
        """Read-only US market clock (is_open, next_open, next_close)."""
        c = self._req("GET", f"{self.base}/v2/clock")
        return {k: c.get(k) for k in ("is_open", "next_open", "next_close", "timestamp")}

    def open_orders(self) -> list[dict]:
        """Read-only list of open orders, stop legs included."""
        keep = ("id", "client_order_id", "symbol", "side", "type", "qty", "filled_qty", "stop_price", "limit_price",
                "status", "time_in_force", "order_class", "submitted_at")
        out = []
        for o in self._req("GET", f"{self.base}/v2/orders", params={"status": "open", "nested": "true"}) or []:
            out.append({k: o.get(k) for k in keep})
            out += [{k: leg.get(k) for k in keep} for leg in o.get("legs") or []]
        return out

    def positions(self):
        return [BrokerPosition(p["symbol"], abs(float(p["qty"])) * (1 if p["side"] == "long" else -1),
                               float(p["avg_entry_price"]), None)
                for p in self._req("GET", f"{self.base}/v2/positions")]

    def submit_market(self, instrument, units, stop_price, take_profit, client_id):
        # gtc, not day: a day order's stop-loss leg would expire at the close and leave the position unprotected.
        order = {"symbol": instrument, "qty": str(int(abs(units))), "side": "buy" if units > 0 else "sell",
                 "type": "market", "time_in_force": "gtc", "client_order_id": client_id[:128]}
        if stop_price is not None:
            order["stop_loss"] = {"stop_price": f"{stop_price:.2f}"}
        if take_profit is not None:
            order["take_profit"] = {"limit_price": f"{take_profit:.2f}"}
        if stop_price is not None or take_profit is not None:
            order["order_class"] = "bracket" if stop_price is not None and take_profit is not None else "oto"
        try:
            r = self.s.post(f"{self.base}/v2/orders", json=order, timeout=self.timeout)
        except requests.RequestException as e:
            return OrderResult("error", None, None, 0, f"network error; reconcile before retrying: {e}")
        try:
            body = r.json() if r.content else {}
        except ValueError:
            body = {}
        if r.status_code >= 400:
            return OrderResult("rejected", None, None, 0, str(body.get("message") or r.status_code))
        if body.get("status") == "filled":
            return OrderResult("filled", body.get("id"), float(body["filled_avg_price"]), float(body["filled_qty"]))
        return OrderResult("pending", body.get("id"), None, 0, f"order {body.get('status')}; Alpaca fills asynchronously")

    def close_all(self):
        try:
            res = self._req("DELETE", f"{self.base}/v2/positions", params={"cancel_orders": "true"}) or []
        except BrokerError as e:
            return [OrderResult("error", None, None, 0, str(e))]
        return [OrderResult("pending" if 200 <= int(x.get("status", 500)) < 300 else "error", None, None, 0,
                            str(x.get("symbol", ""))) for x in res]
