# -*- coding: utf-8 -*-
"""
PHANTRON's brain - a small, dependency-free multi-provider LLM client.

It speaks to whichever backend P7 has configured, using only the Python
standard library (urllib). Supported providers:

  * "ollama"    - a local Ollama server (free, private, runs on your PC)
  * "openai"    - any OpenAI-compatible Chat Completions API
                  (OpenAI, Groq, OpenRouter, LM Studio, Together, ...)
  * "anthropic" - Anthropic's Claude messages API
  * "offline"   - no LLM; PHANTRON still runs commands via the local parser

In "auto" mode the brain picks the first provider that looks configured
(an API key present, or a reachable Ollama). If none is available it falls
back to "offline" so the rest of the assistant keeps working.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from .config import Config

# A short prefix used to signal an internal error string back to callers.
ERR = "__BRAIN_ERR__"


def _http_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    """POST JSON and return parsed JSON. Raises urllib errors on failure."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, val in headers.items():
        req.add_header(key, val)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._provider_cache: Optional[str] = None

    # ─────────────────────────────────────────────────────────────────────
    #  Provider resolution
    # ─────────────────────────────────────────────────────────────────────
    def provider(self) -> str:
        """Return the active provider name, resolving "auto"."""
        configured = (self.cfg.get("brain.provider", "auto") or "auto").lower()
        if configured != "auto":
            return configured
        if self._provider_cache:
            return self._provider_cache

        # auto-detect: prefer an explicit cloud key, else a live Ollama
        if self.cfg.get("brain.openai.api_key"):
            self._provider_cache = "openai"
        elif self.cfg.get("brain.anthropic.api_key"):
            self._provider_cache = "anthropic"
        elif self._ollama_alive():
            self._provider_cache = "ollama"
        else:
            self._provider_cache = "offline"
        return self._provider_cache

    def _ollama_alive(self) -> bool:
        url = self.cfg.get("brain.ollama.url", "http://localhost:11434")
        try:
            req = urllib.request.Request(url.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def status(self) -> Dict[str, str]:
        """Human-friendly status for the UI / startup banner."""
        prov = self.provider()
        if prov == "ollama":
            model = self.cfg.get("brain.ollama.model")
            return {"provider": "ollama", "model": model,
                    "detail": "local Ollama @ " + self.cfg.get("brain.ollama.url")}
        if prov == "openai":
            return {"provider": "openai", "model": self.cfg.get("brain.openai.model"),
                    "detail": self.cfg.get("brain.openai.base_url")}
        if prov == "anthropic":
            return {"provider": "anthropic", "model": self.cfg.get("brain.anthropic.model"),
                    "detail": "Anthropic Claude"}
        return {"provider": "offline", "model": "-",
                "detail": "No LLM configured - command mode only"}

    @property
    def online(self) -> bool:
        return self.provider() != "offline"

    # ─────────────────────────────────────────────────────────────────────
    #  Chat
    # ─────────────────────────────────────────────────────────────────────
    def chat(self, system: str, messages: List[Dict[str, str]]) -> str:
        """
        Send a system prompt + a list of {role, content} messages and return
        the assistant's text. On any failure returns a string starting with
        ERR so callers can decide how to degrade.
        """
        prov = self.provider()
        try:
            if prov == "ollama":
                return self._chat_ollama(system, messages)
            if prov == "openai":
                return self._chat_openai(system, messages)
            if prov == "anthropic":
                return self._chat_anthropic(system, messages)
            return ERR + " offline"
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            return "%s %s %s: %s" % (ERR, prov, exc.code, body)
        except Exception as exc:
            return "%s %s: %s" % (ERR, prov, str(exc)[:160])

    def ask(self, system: str, user: str) -> str:
        """One-shot helper for a single user message."""
        return self.chat(system, [{"role": "user", "content": user}])

    # ---- provider implementations ----
    def _temp(self) -> float:
        return float(self.cfg.get("brain.temperature", 0.6))

    def _max_tokens(self) -> int:
        return int(self.cfg.get("brain.max_tokens", 1024))

    def _chat_ollama(self, system: str, messages: List[Dict[str, str]]) -> str:
        url = self.cfg.get("brain.ollama.url", "http://localhost:11434").rstrip("/")
        payload = {
            "model": self.cfg.get("brain.ollama.model", "llama3.1"),
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": {
                "temperature": self._temp(),
                "num_predict": self._max_tokens(),
            },
        }
        out = _http_json(url + "/api/chat", payload, {}, timeout=120)
        return (out.get("message", {}).get("content") or "").strip()

    def _chat_openai(self, system: str, messages: List[Dict[str, str]]) -> str:
        base = self.cfg.get("brain.openai.base_url", "https://api.openai.com/v1").rstrip("/")
        key = self.cfg.get("brain.openai.api_key", "")
        payload = {
            "model": self.cfg.get("brain.openai.model", "gpt-4o-mini"),
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": self._temp(),
            "max_tokens": self._max_tokens(),
        }
        headers = {"Authorization": "Bearer " + key}
        out = _http_json(base + "/chat/completions", payload, headers, timeout=90)
        choices = out.get("choices") or []
        if choices:
            return (choices[0].get("message", {}).get("content") or "").strip()
        return ""

    def _chat_anthropic(self, system: str, messages: List[Dict[str, str]]) -> str:
        key = self.cfg.get("brain.anthropic.api_key", "")
        payload = {
            "model": self.cfg.get("brain.anthropic.model", "claude-3-5-sonnet-latest"),
            "max_tokens": self._max_tokens(),
            "temperature": self._temp(),
            "system": system,
            "messages": messages,
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        out = _http_json("https://api.anthropic.com/v1/messages", payload, headers, timeout=90)
        parts = out.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return text.strip()
