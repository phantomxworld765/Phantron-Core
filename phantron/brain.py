# -*- coding: utf-8 -*-
"""
PHANTRON's brain - a small, dependency-free multi-provider LLM client.

It speaks to whichever backend P7 has configured, using only the Python
standard library (urllib). Providers, the way P7 wants them:

  ONLINE
    * "openrouter" - OpenRouter (https://openrouter.ai) - one key, hundreds of
                     models incl. free ones. This is the primary online brain.
    * "openai"     - any other OpenAI-compatible API (OpenAI, Groq, LM Studio).
    * "anthropic"  - Anthropic's Claude messages API.

  OFFLINE
    * "ollama"     - a local Ollama server (free, private, runs on your PC).

  FALLBACK
    * "offline"    - no LLM; PHANTRON still runs commands via the local parser.

In "auto" mode the brain prefers an online key first (OpenRouter, then OpenAI,
then Anthropic), then a live local Ollama, then falls back to "offline".
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

        # auto-detect: online keys first (OpenRouter is the primary online
        # brain), then a live local Ollama, else offline.
        if self.cfg.get("brain.openrouter.api_key"):
            self._provider_cache = "openrouter"
        elif self.cfg.get("brain.openai.api_key"):
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
        if prov == "openrouter":
            return {"provider": "openrouter", "model": self.cfg.get("brain.openrouter.model"),
                    "detail": "OpenRouter (online)"}
        if prov == "openai":
            return {"provider": "openai", "model": self.cfg.get("brain.openai.model"),
                    "detail": self.cfg.get("brain.openai.base_url")}
        if prov == "anthropic":
            return {"provider": "anthropic", "model": self.cfg.get("brain.anthropic.model"),
                    "detail": "Anthropic Claude (online)"}
        if prov == "ollama":
            return {"provider": "ollama", "model": self.cfg.get("brain.ollama.model"),
                    "detail": "local Ollama @ " + self.cfg.get("brain.ollama.url") + " (offline)"}
        return {"provider": "offline", "model": "-",
                "detail": "No LLM configured - command mode only"}

    @property
    def online(self) -> bool:
        return self.provider() != "offline"

    @property
    def is_local(self) -> bool:
        return self.provider() == "ollama"

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
            if prov == "openrouter":
                return self._chat_openai_style(*self._openrouter_args(), system, messages)
            if prov == "openai":
                return self._chat_openai_style(*self._openai_args(), system, messages)
            if prov == "anthropic":
                return self._chat_anthropic(system, messages)
            if prov == "ollama":
                return self._chat_ollama(system, messages)
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

    # ─────────────────────────────────────────────────────────────────────
    #  Vision (send an image + question to a vision-capable model)
    # ─────────────────────────────────────────────────────────────────────
    def see(self, question: str, image_b64: str) -> str:
        """
        Ask a vision-capable model about a base64 PNG. Works with OpenAI-style
        endpoints (OpenRouter/OpenAI), Ollama (images list) and Anthropic.
        Returns text, or an ERR string on failure.
        """
        prov = self.provider()
        try:
            if prov in ("openrouter", "openai"):
                base, key, model, headers = (
                    self._openrouter_args() if prov == "openrouter" else self._openai_args()
                )
                vmodel = self.cfg.get("brain.vision_model") or model
                payload = {
                    "model": vmodel,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url",
                         "image_url": {"url": "data:image/png;base64," + image_b64}},
                    ]}],
                    "max_tokens": self._max_tokens(),
                }
                out = _http_json(base + "/chat/completions", payload, headers, timeout=120)
                ch = out.get("choices") or []
                return (ch[0]["message"]["content"].strip() if ch else "")
            if prov == "ollama":
                url = self.cfg.get("brain.ollama.url", "http://localhost:11434").rstrip("/")
                payload = {
                    "model": self.cfg.get("brain.vision_model") or self.cfg.get("brain.ollama.model"),
                    "messages": [{"role": "user", "content": question, "images": [image_b64]}],
                    "stream": False,
                }
                out = _http_json(url + "/api/chat", payload, {}, timeout=120)
                return (out.get("message", {}).get("content") or "").strip()
            if prov == "anthropic":
                key = self.cfg.get("brain.anthropic.api_key", "")
                payload = {
                    "model": self.cfg.get("brain.vision_model") or self.cfg.get("brain.anthropic.model"),
                    "max_tokens": self._max_tokens(),
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": question},
                        {"type": "image", "source": {"type": "base64",
                         "media_type": "image/png", "data": image_b64}},
                    ]}],
                }
                out = _http_json("https://api.anthropic.com/v1/messages", payload,
                                 {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=120)
                parts = out.get("content") or []
                return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            return ERR + " no vision provider"
        except Exception as exc:
            return "%s vision %s: %s" % (ERR, prov, str(exc)[:160])

    # ---- shared bits ----
    def _temp(self) -> float:
        return float(self.cfg.get("brain.temperature", 0.6))

    def _max_tokens(self) -> int:
        return int(self.cfg.get("brain.max_tokens", 1024))

    def _openrouter_args(self):
        base = self.cfg.get("brain.openrouter.base_url", "https://openrouter.ai/api/v1").rstrip("/")
        key = self.cfg.get("brain.openrouter.api_key", "")
        model = self.cfg.get("brain.openrouter.model", "meta-llama/llama-3.1-8b-instruct:free")
        # OpenRouter likes these identifying headers (optional but recommended).
        headers = {
            "Authorization": "Bearer " + key,
            "HTTP-Referer": "https://github.com/phantomxworld765/Phantron-Core",
            "X-Title": "PHANTRON",
        }
        return base, key, model, headers

    def _openai_args(self):
        base = self.cfg.get("brain.openai.base_url", "https://api.openai.com/v1").rstrip("/")
        key = self.cfg.get("brain.openai.api_key", "")
        model = self.cfg.get("brain.openai.model", "gpt-4o-mini")
        headers = {"Authorization": "Bearer " + key}
        return base, key, model, headers

    # ---- provider implementations ----
    def _chat_openai_style(self, base, key, model, headers, system, messages):
        """Shared Chat Completions call for OpenRouter / OpenAI / compatible."""
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": self._temp(),
            "max_tokens": self._max_tokens(),
        }
        out = _http_json(base + "/chat/completions", payload, headers, timeout=90)
        choices = out.get("choices") or []
        if choices:
            return (choices[0].get("message", {}).get("content") or "").strip()
        return ""

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
