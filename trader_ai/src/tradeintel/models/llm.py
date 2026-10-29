"""LLM clients returning validated JSON or None. The system must work with
provider 'none'; LLM output is advisory and never touches risk controls."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

import requests

log = logging.getLogger(__name__)


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> dict | None: ...


def _extract_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


class NullLLM:
    name = "none"

    def complete_json(self, system: str, user: str) -> dict | None:
        return None


class OllamaLLM:
    """Local model served by Ollama (https://ollama.com)."""

    def __init__(self, model: str, host: str | None = None, timeout: int = 60):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.timeout = timeout
        self.name = f"ollama:{model}"

    def complete_json(self, system, user):
        try:
            r = requests.post(f"{self.host}/api/chat", timeout=self.timeout, json={
                "model": self.model, "stream": False, "format": "json",
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            })
            r.raise_for_status()
            return _extract_json(r.json()["message"]["content"])
        except Exception as e:
            log.warning("ollama call failed", extra={"data": {"error": str(e)[:200]}})
            return None


class AnthropicLLM:
    """Anthropic Messages API over HTTPS (optional, needs ANTHROPIC_API_KEY)."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, timeout: int = 60, api_key: str | None = None):
        self.model = model
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.name = f"anthropic:{model}"

    def complete_json(self, system, user):
        if not self.api_key:
            log.warning("ANTHROPIC_API_KEY not set")
            return None
        try:
            r = requests.post(self.URL, timeout=self.timeout, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": self.model, "max_tokens": 1500, "system": system,
                     "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            return _extract_json(text)
        except Exception as e:
            log.warning("anthropic call failed", extra={"data": {"error": str(e)[:200]}})
            return None


def make_llm(provider: str, ollama_model: str, anthropic_model: str, timeout: int = 60) -> LLMClient:
    if provider == "ollama":
        return OllamaLLM(ollama_model, timeout=timeout)
    if provider == "anthropic":
        return AnthropicLLM(anthropic_model, timeout=timeout)
    return NullLLM()
