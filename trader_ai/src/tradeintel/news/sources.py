"""News and calendar sources. Each fetch reports health (latency, error)
so failing or slow sources are visible."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests

from .dedup import dedup_key
from .models import EconEvent, NewsItem
from .sanitize import clean_text

log = logging.getLogger(__name__)
UA = {"User-Agent": "tradeintel/0.1 (research; contact via repo)"}

CURRENCY_KEYWORDS = {
    "USD": ["federal reserve", "fomc", "powell", "u.s.", "us ", "treasury", "nonfarm", "dollar"],
    "EUR": ["ecb", "euro area", "eurozone", "lagarde", "euro "],
    "GBP": ["bank of england", "boe", "sterling", "uk ", "britain"],
    "JPY": ["bank of japan", "boj", "yen", "japan"],
}


@dataclass
class FetchHealth:
    source: str
    ok: bool
    latency_s: float
    items: int
    error: str | None
    at: datetime


def tag_currencies(text: str) -> list[str]:
    t = f" {text.lower()} "
    return sorted({c for c, kws in CURRENCY_KEYWORDS.items() if any(k in t for k in kws)})


class RSSSource:
    def __init__(self, name: str, url: str, reliability: str, timeout: int = 20, session: requests.Session | None = None):
        self.name, self.url, self.reliability, self.timeout = name, url, reliability, timeout
        self.session = session or requests.Session()

    def fetch(self) -> tuple[list[NewsItem], FetchHealth]:
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)
        try:
            r = self.session.get(self.url, headers=UA, timeout=self.timeout)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            return [], FetchHealth(self.name, False, time.monotonic() - t0, 0, str(e)[:300], now)
        items = []
        for e in feed.entries[:200]:
            title = clean_text(e.get("title"), 300)
            if not title:
                continue
            pub = None
            for k in ("published_parsed", "updated_parsed"):
                if e.get(k):
                    pub = datetime(*e[k][:6], tzinfo=timezone.utc)
                    break
            summary = clean_text(e.get("summary"), 1500)
            items.append(NewsItem(source=self.name, reliability=self.reliability, title=title, summary=summary,
                                  url=str(e.get("link", ""))[:500], published_at=pub, ingested_at=now,
                                  dedup_key=dedup_key(title), currencies=tag_currencies(f"{title} {summary}")))
        return items, FetchHealth(self.name, True, time.monotonic() - t0, len(items), None, now)


class ForexFactoryCalendar:
    """Forex Factory's weekly calendar export (JSON). Unofficial; schedule
    and values must be cross-checked with official releases for anything
    that drives orders. Poll sparingly and respect the site's terms."""

    name = "forexfactory"

    def __init__(self, url: str, timeout: int = 20, session: requests.Session | None = None):
        self.url, self.timeout = url, timeout
        self.session = session or requests.Session()

    @staticmethod
    def parse(payload: list[dict], now: datetime) -> list[EconEvent]:
        out = []
        for row in payload:
            try:
                t = datetime.fromisoformat(str(row["date"])).astimezone(timezone.utc)
            except Exception:
                continue
            ccy = clean_text(row.get("country"), 8).upper()
            title = clean_text(row.get("title"), 200)
            impact = str(row.get("impact", "")).lower()
            impact = impact if impact in ("high", "medium", "low", "holiday") else "unknown"
            def val(k):
                v = clean_text(row.get(k), 32)
                return v or None
            out.append(EconEvent(source="forexfactory", event_key=f"{ccy}|{title}|{t.isoformat()}", title=title,
                                 currency=ccy, impact=impact, event_time=t, forecast=val("forecast"),
                                 previous=val("previous"), actual=val("actual"), ingested_at=now))
        return out

    def fetch(self) -> tuple[list[EconEvent], FetchHealth]:
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)
        try:
            r = self.session.get(self.url, headers=UA, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, list):
                raise ValueError("unexpected calendar payload")
        except Exception as e:
            return [], FetchHealth(self.name, False, time.monotonic() - t0, 0, str(e)[:300], now)
        ev = self.parse(payload, now)
        return ev, FetchHealth(self.name, True, time.monotonic() - t0, len(ev), None, now)
