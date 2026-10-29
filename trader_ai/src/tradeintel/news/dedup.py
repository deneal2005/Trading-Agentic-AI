"""Deduplication of syndicated / repeated stories."""

from __future__ import annotations

import hashlib
import re

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "with", "is", "after", "from"}


def tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%.]+", title.lower()) if w not in _STOP}


def dedup_key(title: str) -> str:
    return hashlib.sha1(" ".join(sorted(tokens(title))).encode()).hexdigest()[:16]


def near_duplicate(a: str, b: str, threshold: float = 0.8) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold
