"""External text is untrusted data. It is cleaned, truncated and always
passed to models as quoted content, never as instructions."""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")
# Phrases typical of prompt-injection attempts; flagged, not executed.
_INJECTION = re.compile(r"(ignore (all|any|previous|prior) instructions|system prompt|you are now|disregard .{0,20}rules|"
                        r"disable (the )?risk|override (the )?(risk|limits))", re.I)


def clean_text(s: str | None, max_chars: int = 2000) -> str:
    if not s:
        return ""
    s = html.unescape(_TAG.sub(" ", s))
    s = _CTRL.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s[:max_chars]


def injection_suspected(s: str) -> bool:
    return bool(_INJECTION.search(s or ""))
