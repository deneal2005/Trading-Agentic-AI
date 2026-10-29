"""Searchable, source-attributed knowledge base (SQLite FTS5 / BM25).

Every chunk records where it came from (source, license, path, version) and
an evidence level. Ingested documents are data: they are never executed and
can never change configuration or risk limits.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..data.store import connect
from ..news.sanitize import clean_text, injection_suspected

EVIDENCE_LEVELS = ("established_fact", "research_finding", "empirical_observation", "hypothesis", "unverified_claim")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_docs (
  doc_id TEXT PRIMARY KEY, source TEXT, license TEXT, path TEXT, version TEXT,
  title TEXT, evidence_level TEXT, ingested_at TEXT, flags TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks USING fts5(
  doc_id UNINDEXED, heading, body, tokenize='porter unicode61'
);
"""


@dataclass
class Hit:
    doc_id: str
    title: str
    heading: str
    snippet: str
    source: str
    license: str
    path: str
    evidence_level: str
    score: float


def chunk_markdown(text: str, max_chars: int = 1500) -> list[tuple[str, str]]:
    """Split on headings, then cap chunk size."""
    parts = re.split(r"(?m)^(#{1,4} .+)$", text)
    chunks: list[tuple[str, str]] = []
    heading = ""
    for p in parts:
        if re.match(r"^#{1,4} ", p or ""):
            heading = p.lstrip("# ").strip()
            continue
        body = p.strip()
        while body:
            chunks.append((heading, body[:max_chars]))
            body = body[max_chars:]
    return chunks


class KnowledgeBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with connect(db_path) as con:
            con.executescript(SCHEMA)

    def add_document(self, text: str, *, source: str, license: str, path: str, version: str,
                     evidence_level: str = "unverified_claim", title: str | None = None) -> str:
        if evidence_level not in EVIDENCE_LEVELS:
            raise ValueError(f"evidence_level must be one of {EVIDENCE_LEVELS}")
        doc_id = hashlib.sha1(f"{source}|{path}".encode()).hexdigest()[:16]
        flags = "possible_prompt_injection" if injection_suspected(text) else ""
        title = title or Path(path).stem
        with connect(self.db_path) as con:
            con.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
            con.execute("INSERT OR REPLACE INTO kb_docs VALUES (?,?,?,?,?,?,?,?,?)",
                        (doc_id, source, license, path, version, title, evidence_level,
                         datetime.now(timezone.utc).isoformat(), flags))
            for heading, body in chunk_markdown(text):
                con.execute("INSERT INTO kb_chunks (doc_id, heading, body) VALUES (?,?,?)",
                            (doc_id, clean_text(heading, 200), clean_text(body, 2000)))
        return doc_id

    def search(self, query: str, k: int = 5, evidence_levels: tuple[str, ...] | None = None) -> list[Hit]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        fts = " OR ".join(f'"{t}"' for t in terms)
        q = ("SELECT c.doc_id, d.title, c.heading, snippet(kb_chunks, 2, '[', ']', '...', 24), d.source, d.license, "
             "d.path, d.evidence_level, bm25(kb_chunks) FROM kb_chunks c JOIN kb_docs d ON d.doc_id=c.doc_id "
             "WHERE kb_chunks MATCH ?")
        args: list = [fts]
        if evidence_levels:
            q += f" AND d.evidence_level IN ({','.join('?' * len(evidence_levels))})"
            args += list(evidence_levels)
        q += " ORDER BY bm25(kb_chunks) LIMIT ?"
        args.append(k)
        with connect(self.db_path) as con:
            try:
                rows = con.execute(q, args).fetchall()
            except sqlite3.OperationalError:
                return []
        return [Hit(*r[:8], score=-float(r[8])) for r in rows]

    def stats(self) -> dict:
        with connect(self.db_path) as con:
            docs = con.execute("SELECT source, evidence_level, COUNT(*) FROM kb_docs GROUP BY source, evidence_level").fetchall()
            n = con.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
        return {"chunks": n, "docs": [{"source": s, "evidence_level": e, "count": c} for s, e, c in docs]}
