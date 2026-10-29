"""News and economic-event records. Every record carries its source and
three timestamps: published (by the source), event (when it happens) and
ingested (when we saw it). Point-in-time joins use ingested/published, never
anything later."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Reliability = Literal["official", "reputable", "aggregator", "unverified"]
Impact = Literal["high", "medium", "low", "holiday", "unknown"]


class NewsItem(BaseModel):
    source: str
    reliability: Reliability
    title: str
    summary: str = ""
    url: str = ""
    published_at: datetime | None
    ingested_at: datetime
    dedup_key: str
    currencies: list[str] = Field(default_factory=list)


class EconEvent(BaseModel):
    source: str
    event_key: str              # stable id: currency|title|scheduled time
    title: str
    currency: str
    impact: Impact
    event_time: datetime
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    ingested_at: datetime
