"""SQLite storage for bars with provenance (source, ingestion time, hash)."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  instrument TEXT NOT NULL, timeframe TEXT NOT NULL, ts TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  source TEXT NOT NULL, ingested_at TEXT NOT NULL,
  PRIMARY KEY (instrument, timeframe, ts, source)
);
CREATE TABLE IF NOT EXISTS ingestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, instrument TEXT, timeframe TEXT, source TEXT,
  rows INTEGER, first_ts TEXT, last_ts TEXT, data_hash TEXT, issues TEXT, ingested_at TEXT
);
"""


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        yield con
        con.commit()
    finally:
        con.close()


def frame_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()[:16]


class BarStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def upsert(self, df: pd.DataFrame, instrument: str, timeframe: str, source: str, issues: list[str] | None = None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (instrument, timeframe, ts.isoformat(), r.open, r.high, r.low, r.close,
             None if pd.isna(r.volume) else float(r.volume), source, now)
            for ts, r in df.iterrows()
        ]
        h = frame_hash(df)
        with connect(self.db_path) as con:
            con.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            con.execute(
                "INSERT INTO ingestions (instrument,timeframe,source,rows,first_ts,last_ts,data_hash,issues,ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (instrument, timeframe, source, len(df), str(df.index.min()), str(df.index.max()), h, "; ".join(issues or []), now),
            )
        return h

    def load(self, instrument: str, timeframe: str, source: str | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume, source FROM bars WHERE instrument=? AND timeframe=?"
        args: list = [instrument, timeframe]
        if source:
            q += " AND source=?"
            args.append(source)
        with connect(self.db_path) as con:
            df = pd.read_sql_query(q + " ORDER BY ts", con, params=args)
        if df.empty:
            return df
        sources = df["source"].unique()
        if len(sources) > 1:
            raise ValueError(f"multiple sources stored for {instrument} {timeframe}: {list(sources)}; pass source=")
        df.index = pd.to_datetime(df.pop("ts"), utc=True, format="ISO8601")
        df.attrs["source"] = sources[0]
        return df.drop(columns="source")
