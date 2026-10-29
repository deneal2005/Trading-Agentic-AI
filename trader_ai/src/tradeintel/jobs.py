"""A shared record of long-running work (checks, data refreshes, tests).

Every run is a row in SQLite, so the app, the background service and the CLI
all see the same truth: what is running, which step it is on, how long it has
taken, and how it ended. A running job writes a sign of life every few
seconds; if that stops (the process crashed or was closed), readers report it
as stopped instead of showing it as running forever.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .data.store import connect

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, label TEXT, source TEXT, pid INTEGER,
  state TEXT, steps TEXT, step TEXT, done_steps TEXT, expected_s REAL,
  started_at TEXT, updated_at TEXT, finished_at TEXT, result TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
"""
BEAT_S = 3.0       # how often a running job writes a sign of life
LOST_S = 30.0      # no sign of life for this long = the job is gone


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    def __init__(self, db_path: Path):
        self.db = db_path
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def start(self, kind: str, label: str, steps: list[str], source: str, expected_s: float) -> int:
        t = _now().isoformat()
        with connect(self.db) as con:
            cur = con.execute(
                "INSERT INTO jobs (kind,label,source,pid,state,steps,step,done_steps,expected_s,started_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (kind, label, source, os.getpid(), "running", json.dumps(steps), steps[0] if steps else "", "[]", expected_s, t, t))
            return int(cur.lastrowid)

    def step(self, job_id: int, name: str) -> None:
        with connect(self.db) as con:
            row = con.execute("SELECT step, done_steps FROM jobs WHERE id=?", (job_id,)).fetchone()
            done = json.loads(row[1] or "[]")
            if row[0] and row[0] != name and row[0] not in done:
                done.append(row[0])
            con.execute("UPDATE jobs SET step=?, done_steps=?, updated_at=? WHERE id=?",
                        (name, json.dumps(done), _now().isoformat(), job_id))

    def beat(self, job_id: int) -> None:
        with connect(self.db) as con:
            con.execute("UPDATE jobs SET updated_at=? WHERE id=? AND state='running'", (_now().isoformat(), job_id))

    def finish(self, job_id: int, ok: bool, result=None, error: str | None = None) -> None:
        t = _now().isoformat()
        with connect(self.db) as con:
            row = con.execute("SELECT step, done_steps FROM jobs WHERE id=?", (job_id,)).fetchone()
            done = json.loads(row[1] or "[]")
            if ok and row[0] and row[0] not in done:
                done.append(row[0])  # the step it ended on completed
            con.execute("UPDATE jobs SET state=?, done_steps=?, finished_at=?, updated_at=?, result=?, error=? WHERE id=?",
                        ("succeeded" if ok else "failed", json.dumps(done), t, t,
                         json.dumps(result, default=str) if result is not None else None, error, job_id))

    def _view(self, r) -> dict:
        r = dict(r)
        for k in ("steps", "done_steps", "result"):
            r[k] = json.loads(r[k]) if r[k] else ([] if k != "result" else None)
        now = _now()
        start = datetime.fromisoformat(r["started_at"])
        end = datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else now
        r["elapsed_s"] = round((end - start).total_seconds(), 1)
        if r["state"] == "running":
            quiet = (now - datetime.fromisoformat(r["updated_at"])).total_seconds()
            if quiet > LOST_S:  # nobody is updating it: the process stopped
                r["state"] = "lost"
                r["error"] = f"no sign of life for {int(quiet)} s; the program running it probably stopped"
            elif r["expected_s"] and r["elapsed_s"] > 2 * r["expected_s"]:
                r["slow"] = True
        return r

    def get(self, job_id: int) -> dict | None:
        with connect(self.db) as con:
            con.row_factory = __import__("sqlite3").Row
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._view(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        with connect(self.db) as con:
            con.row_factory = __import__("sqlite3").Row
            rows = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._view(r) for r in rows]

    def running(self, kind: str | None = None) -> list[dict]:
        with connect(self.db) as con:
            con.row_factory = __import__("sqlite3").Row
            q, args = "SELECT * FROM jobs WHERE state='running'", []
            if kind:
                q += " AND kind=?"
                args.append(kind)
            rows = con.execute(q + " ORDER BY id", args).fetchall()
        return [j for j in (self._view(r) for r in rows) if j["state"] == "running"]


def _execute(store: JobStore, job_id: int, fn: Callable[[Callable[[str], None]], dict]) -> dict:
    """Run fn(progress) for a job: heartbeat while it runs, record the outcome. Returns fn's result."""
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(BEAT_S):
            try:
                store.beat(job_id)
            except Exception:  # a locked DB for one beat isn't fatal
                pass

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        res = fn(lambda name: store.step(job_id, name))
        ok = not (isinstance(res, dict) and res.get("ok") is False)
        store.finish(job_id, ok, res, None if ok else str(res.get("error")))
        return res
    except Exception as e:
        log.exception("job failed", extra={"data": {"job": job_id}})
        store.finish(job_id, False, None, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        stop.set()


def run_job(store: JobStore, kind: str, label: str, steps: list[str], fn, source: str, expected_s: float = 10) -> dict:
    """Run in the current thread (CLI, service). Recorded the same way as app jobs."""
    return _execute(store, store.start(kind, label, steps, source, expected_s), fn)


_start_lock = threading.Lock()


def start_job(store: JobStore, kind: str, label: str, steps: list[str], fn, source: str = "app",
              expected_s: float = 10) -> tuple[dict, bool]:
    """Start in a background thread and return at once. If a job of the same kind is already running
    (from any tab or process), return that one instead of starting a duplicate."""
    with _start_lock:
        busy = store.running(kind)
        if busy:
            return busy[0], False
        job_id = store.start(kind, label, steps, source, expected_s)
    threading.Thread(target=_execute, args=(store, job_id, fn), daemon=True).start()
    return store.get(job_id), True


def mark_abandoned(store: JobStore, source: str = "app") -> None:
    """At app start: jobs this app left running when it was closed can't still be running."""
    with connect(store.db) as con:
        con.execute("UPDATE jobs SET state='failed', error='the app was closed while this was running', finished_at=? "
                    "WHERE state='running' AND source=? AND pid!=?", (_now().isoformat(), source, os.getpid()))
