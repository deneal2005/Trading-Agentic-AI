"""Paper broker: simulated fills against externally supplied quotes,
persisted in SQLite so state survives restarts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..data.store import connect
from .broker import AccountInfo, Broker, BrokerError, BrokerPosition, OrderResult, Quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK (id=1), currency TEXT, balance REAL);
CREATE TABLE IF NOT EXISTS paper_positions (instrument TEXT PRIMARY KEY, units REAL, avg_price REAL, stop REAL, tp REAL);
CREATE TABLE IF NOT EXISTS paper_orders (client_id TEXT PRIMARY KEY, instrument TEXT, units REAL, fill REAL, status TEXT, at TEXT);
"""


class PaperBroker(Broker):
    def __init__(self, db_path: Path, initial_balance: float = 10_000.0, currency: str = "USD"):
        self.db = db_path
        self._quotes: dict[str, Quote] = {}
        with connect(db_path) as con:
            con.executescript(SCHEMA)
            if not con.execute("SELECT 1 FROM paper_account").fetchone():
                con.execute("INSERT INTO paper_account VALUES (1, ?, ?)", (currency, initial_balance))

    def set_quote(self, q: Quote) -> None:
        self._quotes[q.instrument] = q

    def quote(self, instrument):
        if instrument not in self._quotes:
            raise BrokerError(f"no quote for {instrument}")
        return self._quotes[instrument]

    def _unrealized(self) -> float:
        total = 0.0
        for p in self.positions():
            q = self._quotes.get(p.instrument)
            if q:
                px = q.bid if p.units > 0 else q.ask
                total += (px - p.avg_price) * p.units  # quote ccy == account ccy assumed (USD-quoted pairs)
        return total

    def account(self):
        with connect(self.db) as con:
            ccy, bal = con.execute("SELECT currency, balance FROM paper_account").fetchone()
        return AccountInfo("paper-1", "paper", ccy, bal, bal + self._unrealized())

    def positions(self):
        with connect(self.db) as con:
            rows = con.execute("SELECT instrument, units, avg_price, stop FROM paper_positions WHERE units != 0").fetchall()
        return [BrokerPosition(i, u, a, s) for i, u, a, s in rows]

    def known_client_ids(self) -> set[str]:
        with connect(self.db) as con:
            return {r[0] for r in con.execute("SELECT client_id FROM paper_orders")}

    def submit_market(self, instrument, units, stop_price, take_profit, client_id):
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.db) as con:
            if con.execute("SELECT 1 FROM paper_orders WHERE client_id=?", (client_id,)).fetchone():
                return OrderResult("rejected", None, None, 0, "duplicate client_id")
            q = self._quotes.get(instrument)
            if q is None:
                return OrderResult("rejected", None, None, 0, "no quote")
            fill = q.ask if units > 0 else q.bid
            row = con.execute("SELECT units, avg_price FROM paper_positions WHERE instrument=?", (instrument,)).fetchone()
            if row and row[0] != 0:
                # Only open-from-flat is supported; closing goes through close_all.
                return OrderResult("rejected", None, None, 0, "position exists; netting not supported in paper broker")
            con.execute("INSERT OR REPLACE INTO paper_positions VALUES (?,?,?,?,?)", (instrument, units, fill, stop_price, take_profit))
            con.execute("INSERT INTO paper_orders VALUES (?,?,?,?,?,?)", (client_id, instrument, units, fill, "filled", now))
        return OrderResult("filled", client_id, fill, abs(units))

    def close_all(self):
        out = []
        with connect(self.db) as con:
            for inst, units, avg in con.execute("SELECT instrument, units, avg_price FROM paper_positions WHERE units != 0").fetchall():
                q = self._quotes.get(inst)
                if q is None:
                    out.append(OrderResult("error", None, None, 0, f"no quote to close {inst}"))
                    continue
                px = q.bid if units > 0 else q.ask
                con.execute("UPDATE paper_account SET balance = balance + ?", ((px - avg) * units,))
                con.execute("UPDATE paper_positions SET units=0 WHERE instrument=?", (inst,))
                out.append(OrderResult("filled", None, px, abs(units), json.dumps({"closed": inst})))
        return out
