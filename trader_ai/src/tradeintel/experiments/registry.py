"""Experiment registry: every run is recorded, including failures, so the
number of trials behind a result is always visible."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..data.store import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,              -- backtest | walkforward | holdout | model | ablation
  strategy TEXT, params TEXT, instrument TEXT, timeframe TEXT,
  data_source TEXT, data_hash TEXT, config_hash TEXT, code_version TEXT,
  n_trials INTEGER, metrics TEXT, verdict TEXT, notes TEXT
);
"""


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return str(o)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def code_version(root: Path | None = None) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=root, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def stable_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Registry:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def record(self, kind: str, *, strategy: str | None, params: dict | None, instrument: str, timeframe: str,
               data_source: str, data_hash: str, config: dict, n_trials: int, metrics: dict,
               verdict: str | None = None, notes: str = "") -> int:
        with connect(self.db_path) as con:
            cur = con.execute(
                "INSERT INTO experiments (created_at,kind,strategy,params,instrument,timeframe,data_source,data_hash,"
                "config_hash,code_version,n_trials,metrics,verdict,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), kind, strategy, json.dumps(_clean(params or {})), instrument,
                 timeframe, data_source, data_hash, stable_hash(config), code_version(self.db_path.parent),
                 n_trials, json.dumps(_clean(metrics), default=str), verdict, notes),
            )
            return int(cur.lastrowid)

    def list(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM experiments"
        args: list = []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with connect(self.db_path) as con:
            con.row_factory = __import__("sqlite3").Row
            rows = [dict(r) for r in con.execute(q, args)]
        for r in rows:
            r["metrics"] = json.loads(r["metrics"] or "{}")
            r["params"] = json.loads(r["params"] or "{}")
        return rows

    def holdout_uses(self, strategy: str, data_hash: str) -> int:
        with connect(self.db_path) as con:
            return con.execute("SELECT COUNT(*) FROM experiments WHERE kind='holdout' AND strategy=? AND data_hash=?",
                               (strategy, data_hash)).fetchone()[0]

    def total_trials(self, strategy: str) -> int:
        with connect(self.db_path) as con:
            return con.execute("SELECT COALESCE(SUM(n_trials),0) FROM experiments WHERE strategy=?", (strategy,)).fetchone()[0]
