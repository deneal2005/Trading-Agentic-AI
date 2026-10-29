"""OANDA v20 REST client restricted to the PRACTICE environment.

The live host is not reachable through this class: the base URL is fixed
to the practice server, and the account is verified before use. This code
has not yet been exercised against a real practice account (see
PROJECT_PLAN.md); treat it as untested until the integration test passes.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from .broker import AccountInfo, Broker, BrokerError, BrokerPosition, OrderResult, Quote

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_HOST_FRAGMENT = "api-fxtrade"


def parse_oanda_time(t: str) -> datetime:
    """OANDA RFC3339 with up to 9 fractional digits -> aware datetime."""
    t = t.rstrip("Z")
    if "." in t:
        head, frac = t.split(".", 1)
        t = f"{head}.{frac[:6].ljust(6, '0')}"
    return datetime.fromisoformat(t).replace(tzinfo=timezone.utc)


def oanda_symbol(instrument: str) -> str:
    return f"{instrument[:3]}_{instrument[3:]}" if "_" not in instrument else instrument


class OandaPracticeBroker(Broker):
    def __init__(self, token: str | None = None, account_id: str | None = None, timeout: int = 15,
                 session: requests.Session | None = None):
        env = os.environ.get("OANDA_ENVIRONMENT", "practice")
        if env != "practice":
            raise BrokerError("OANDA_ENVIRONMENT must be 'practice'; live trading is not supported")
        self.token = token or os.environ.get("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")
        if not self.token or not self.account_id:
            raise BrokerError("OANDA_API_TOKEN and OANDA_ACCOUNT_ID must be set (see .env.example)")
        self.base = PRACTICE_URL
        assert LIVE_HOST_FRAGMENT not in self.base
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})

    def _get(self, path: str, **params):
        try:
            r = self.s.get(f"{self.base}{path}", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise BrokerError(f"OANDA GET {path} failed: {e}") from e

    def account(self):
        a = self._get(f"/v3/accounts/{self.account_id}/summary")["account"]
        return AccountInfo(self.account_id, "practice", a["currency"], float(a["balance"]), float(a["NAV"]))

    def quote(self, instrument):
        p = self._get(f"/v3/accounts/{self.account_id}/pricing", instruments=oanda_symbol(instrument))["prices"][0]
        if not p.get("tradeable", False):
            raise BrokerError(f"{instrument} not tradeable now")
        return Quote(instrument, float(p["bids"][0]["price"]), float(p["asks"][0]["price"]),
                     parse_oanda_time(p["time"]))

    def positions(self):
        out = []
        for p in self._get(f"/v3/accounts/{self.account_id}/openPositions")["positions"]:
            for side in ("long", "short"):
                u = float(p[side]["units"])
                if u:
                    out.append(BrokerPosition(p["instrument"].replace("_", ""), u, float(p[side]["averagePrice"]), None))
        return out

    def submit_market(self, instrument, units, stop_price, take_profit, client_id):
        order = {"type": "MARKET", "instrument": oanda_symbol(instrument), "units": str(int(units)),
                 "timeInForce": "FOK", "positionFill": "DEFAULT", "clientExtensions": {"id": client_id[:120]}}
        if stop_price is not None:
            order["stopLossOnFill"] = {"price": f"{stop_price:.5f}"}
        if take_profit is not None:
            order["takeProfitOnFill"] = {"price": f"{take_profit:.5f}"}
        try:
            r = self.s.post(f"{self.base}/v3/accounts/{self.account_id}/orders", json={"order": order}, timeout=self.timeout)
        except requests.RequestException as e:
            return OrderResult("error", None, None, 0, f"network error; reconcile before retrying: {e}")
        body = r.json() if r.content else {}
        if "orderFillTransaction" in body:
            f = body["orderFillTransaction"]
            return OrderResult("filled", f.get("orderID"), float(f["price"]), abs(float(f["units"])))
        if "orderCancelTransaction" in body or r.status_code >= 400:
            reason = body.get("orderCancelTransaction", {}).get("reason") or body.get("errorMessage") or r.status_code
            return OrderResult("rejected", None, None, 0, str(reason))
        return OrderResult("pending", body.get("orderCreateTransaction", {}).get("id"), None, 0, "not filled yet")

    def close_all(self):
        out = []
        for p in self.positions():
            side = "longUnits" if p.units > 0 else "shortUnits"
            try:
                r = self.s.put(f"{self.base}/v3/accounts/{self.account_id}/positions/{oanda_symbol(p.instrument)}/close",
                               json={side: "ALL"}, timeout=self.timeout)
                out.append(OrderResult("filled" if r.ok else "error", None, None, abs(p.units), r.text[:200]))
            except requests.RequestException as e:
                out.append(OrderResult("error", None, None, 0, str(e)))
        return out
