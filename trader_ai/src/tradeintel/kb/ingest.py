"""Knowledge ingestion: local strategy cards and reference repositories.

Reference repositories are cloned into var/refs and only their Markdown is
read. Nothing from them is executed or imported.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import yaml

from .store import KnowledgeBase

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 200_000


def load_sources(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["sources"]


def clone_or_update(url: str, dest: Path, timeout: int = 300) -> str:
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=False, timeout=timeout)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(dest)], check=True, timeout=timeout)
    return subprocess.run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


def ingest_repo(kb: KnowledgeBase, root: Path, source: dict, version: str) -> int:
    n = 0
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        if any(part.startswith(".") or part in ("node_modules", "tests", "test") for part in rel.parts):
            continue
        if f.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        kb.add_document(text, source=source["name"], license=source["license"], path=str(rel), version=version,
                        evidence_level=source.get("default_evidence_level", "unverified_claim"))
        n += 1
    return n


def ingest_strategy_cards(kb: KnowledgeBase, cards_dir: Path) -> int:
    n = 0
    for f in sorted(cards_dir.glob("*.yaml")):
        card = yaml.safe_load(f.read_text())
        lines = [f"# {card['name']}", f"Family: {card['family']}", f"Status: {card['status']}"]
        for key, val in card.items():
            if key in ("name", "family", "status", "evidence_level", "version"):
                continue
            lines.append(f"## {key.replace('_', ' ').title()}")
            lines.append(yaml.safe_dump(val, sort_keys=False).strip() if not isinstance(val, str) else val)
        kb.add_document("\n".join(lines), source="local_strategy_cards", license="project", path=f.name,
                        version=str(card.get("version", "1")), evidence_level=card.get("evidence_level", "hypothesis"),
                        title=card["name"])
        n += 1
    return n


def ingest_markdown_dir(kb: KnowledgeBase, d: Path, source: str = "local_concepts") -> int:
    n = 0
    for f in sorted(d.glob("*.md")):
        text = f.read_text()
        level = "hypothesis"
        if text.startswith("---"):
            front = yaml.safe_load(text.split("---", 2)[1]) or {}
            level = front.get("evidence_level", level)
            text = text.split("---", 2)[2]
        kb.add_document(text, source=source, license="project", path=f.name, version="1", evidence_level=level)
        n += 1
    return n
