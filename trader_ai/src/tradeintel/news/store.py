"""SQLite persistence for news, events (with revision history) and source health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..data.store import connect
from .dedup import near_duplicate
from .models import EconEvent, NewsItem
from .sources import FetchHealth

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT UNIQUE, source TEXT, reliability TEXT,
  title TEXT, summary TEXT, url TEXT, published_at TEXT, ingested_at TEXT, currencies TEXT,
  duplicate_of INTEGER, analysis TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_key TEXT PRIMARY KEY, source TEXT, title TEXT, currency TEXT, impact TEXT, event_time TEXT,
  forecast TEXT, previous TEXT, actual TEXT, first_seen TEXT, last_seen TEXT, analysis TEXT
);
CREATE TABLE IF NOT EXISTS event_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT, field TEXT, old TEXT, new TEXT, seen_at TEXT
);
CREATE TABLE IF NOT EXISTS source_health (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, ok INTEGER, latency_s REAL, items INTEGER, error TEXT, at TEXT
);
"""


class NewsStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def add_news(self, items: list[NewsItem], lookback_hours: int = 48) -> int:
        """Insert new items; exact and near duplicates are skipped. Returns inserted count."""
        added = 0
        since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        with connect(self.db_path) as con:
            recent = [r[0] for r in con.execute("SELECT title FROM news WHERE ingested_at >= ?", (since,))]
            for it in items:
                if con.execute("SELECT 1 FROM news WHERE dedup_key=?", (it.dedup_key,)).fetchone():
                    continue
                if any(near_duplicate(it.title, t) for t in recent):
                    continue
                con.execute(
                    "INSERT INTO news (dedup_key,source,reliability,title,summary,url,published_at,ingested_at,currencies) VALUES (?,?,?,?,?,?,?,?,?)",
                    (it.dedup_key, it.source, it.reliability, it.title, it.summary, it.url,
                     it.published_at.isoformat() if it.published_at else None, it.ingested_at.isoformat(), ",".join(it.currencies)),
                )
                recent.append(it.title)
                added += 1
        return added

    def upsert_events(self, events: list[EconEvent]) -> tuple[int, int]:
        """Returns (new, revised). Changes to forecast/previous/actual are kept as revisions."""
        new = revised = 0
        with connect(self.db_path) as con:
            for e in events:
                row = con.execute("SELECT forecast, previous, actual FROM events WHERE event_key=?", (e.event_key,)).fetchone()
                now = e.ingested_at.isoformat()
                if row is None:
                    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                                (e.event_key, e.source, e.title, e.currency, e.impact, e.event_time.isoformat(),
                                 e.forecast, e.previous, e.actual, now, now))
                    new += 1
                    continue
                changed = False
                for f, old in zip(("forecast", "previous", "actual"), row):
                    nv = getattr(e, f)
                    if nv != old and nv is not None:
                        con.execute("INSERT INTO event_revisions (event_key,field,old,new,seen_at) VALUES (?,?,?,?,?)",
                                    (e.event_key, f, old, nv, now))
                        changed = True
                con.execute("UPDATE events SET forecast=COALESCE(?,forecast), previous=COALESCE(?,previous), "
                            "actual=COALESCE(?,actual), impact=?, last_seen=? WHERE event_key=?",
                            (e.forecast, e.previous, e.actual, e.impact, now, e.event_key))
                revised += changed
        return new, revised

    def record_health(self, h: FetchHealth) -> None:
        with connect(self.db_path) as con:
            con.execute("INSERT INTO source_health (source,ok,latency_s,items,error,at) VALUES (?,?,?,?,?,?)",
                        (h.source, int(h.ok), h.latency_s, h.items, h.error, h.at.isoformat()))

    def events(self, start: datetime | None = None, end: datetime | None = None, impact: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        if start:
            q += " AND event_time >= ?"; args.append(start.isoformat())
        if end:
            q += " AND event_time <= ?"; args.append(end.isoformat())
        if impact:
            q += " AND impact = ?"; args.append(impact)
        with connect(self.db_path) as con:
            con.row_factory = __import__("sqlite3").Row
            return [dict(r) for r in con.execute(q + " ORDER BY event_time", args)]

    def recent_news(self, limit: int = 50) -> list[dict]:
        with connect(self.db_path) as con:
            con.row_factory = __import__("sqlite3").Row
            return [dict(r) for r in con.execute("SELECT * FROM news ORDER BY ingested_at DESC LIMIT ?", (limit,))]

    def save_analysis(self, table: str, key_col: str, key, analysis_json: str) -> None:
        assert table in ("news", "events") and key_col in ("id", "event_key")
        with connect(self.db_path) as con:
            con.execute(f"UPDATE {table} SET analysis=? WHERE {key_col}=?", (analysis_json, key))

    def health(self, limit: int = 50) -> list[dict]:
        with connect(self.db_path) as con:
            con.row_factory = __import__("sqlite3").Row
            return [dict(r) for r in con.execute(
                "SELECT source, ok, latency_s, items, error, MAX(at) AS at FROM source_health GROUP BY source ORDER BY source LIMIT ?", (limit,))]
