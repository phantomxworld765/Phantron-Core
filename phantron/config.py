# -*- coding: utf-8 -*-
"""
PHANTRON configuration.

Loads ``config.json`` (next to the repo root), deep-merges it over sensible
defaults, and writes back a complete file so P7 always has every option
visible and editable. API keys can also come from environment variables, so
nothing secret has to live in the file if you would rather not.

Zero-config friendly: if you change nothing, PHANTRON still boots and runs in
text mode. To give it a real "brain" you only need ONE of:
  * a running local Ollama  (free, private)        -> brain.provider = "ollama"
  * any OpenAI-compatible API key (OpenAI/Groq/...) -> brain.provider = "openai"
  * an Anthropic (Claude) API key                  -> brain.provider = "anthropic"
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

# config.json lives at the repo root (one level above this package)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"

DEFAULTS: Dict[str, Any] = {
    "user_name": "P7",
    "assistant_name": "PHANTRON",
    # how PHANTRON should talk: "hinglish" | "hindi" | "english"
    "language": "hinglish",

    "brain": {
        # "auto" picks the first provider that looks configured.
        # Explicit options: "ollama" | "openai" | "anthropic" | "offline"
        "provider": "auto",
        "temperature": 0.6,
        "max_tokens": 1024,
        # how many recent turns of conversation to remember
        "memory_turns": 12,

        # Local, free, private. Install from https://ollama.com then:
        #   ollama pull llama3.1     (or qwen2.5, mistral, etc.)
        "ollama": {
            "url": "http://localhost:11434",
            "model": "llama3.1",
        },

        # OpenAI-COMPATIBLE. One block covers many providers - just change
        # base_url + model + key:
        #   OpenAI     : https://api.openai.com/v1            gpt-4o-mini
        #   Groq(free) : https://api.groq.com/openai/v1       llama-3.3-70b-versatile
        #   OpenRouter : https://openrouter.ai/api/v1         (many free models)
        #   LM Studio  : http://localhost:1234/v1             (local, free)
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },

        # Anthropic Claude
        "anthropic": {
            "api_key": "",
            "model": "claude-3-5-sonnet-latest",
        },
    },

    "server": {
        "host": "127.0.0.1",
        "port": 8770,
        "open_browser": True,
    },

    "voice": {
        "input": True,            # listen for the wake word (needs a mic + libs)
        "output": True,           # speak replies (needs a TTS engine)
        "wake_word": "phantron",
        "stt_language": "en-IN",  # speech-to-text language hint
        "tts_voice": "",          # leave empty for the system default voice
    },

    "agent": {
        "autonomous": True,       # let the brain decide and run tools
        "max_steps": 6,           # safety bound on autonomous tool calls
        "safe_mode": True,        # block catastrophic shell commands
        "allow_shutdown": False,  # block shutdown/restart unless True
        "command_timeout": 120,   # seconds per shell command
    },

    "paths": {
        # leave empty to default to ~/Phantron/Projects and ~/Phantron/Files
        "projects_dir": "",
        "files_dir": "",
    },
}

# Environment variables that can fill in secrets without editing the file.
_ENV_KEYS = {
    ("brain", "openai", "api_key"): ["OPENAI_API_KEY", "GROQ_API_KEY", "PHANTRON_API_KEY"],
    ("brain", "anthropic", "api_key"): ["ANTHROPIC_API_KEY"],
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``over`` into a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _apply_env(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fill empty secret fields from environment variables when present."""
    for path, env_names in _ENV_KEYS.items():
        node = cfg
        for part in path[:-1]:
            node = node.setdefault(part, {})
        leaf = path[-1]
        if not node.get(leaf):
            for env_name in env_names:
                if os.environ.get(env_name):
                    node[leaf] = os.environ[env_name]
                    break
    return cfg


class Config:
    """Thin wrapper around the merged config dict with dotted-path access."""

    def __init__(self, data: Dict[str, Any], path: Path = CONFIG_FILE):
        self.data = data
        self.path = path

    # ---- dotted access: cfg.get("brain.provider", "auto") ----
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> None:
        node = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    # ---- convenience paths ----
    def projects_dir(self) -> Path:
        raw = self.get("paths.projects_dir") or (Path.home() / "Phantron" / "Projects")
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def files_dir(self) -> Path:
        raw = self.get("paths.files_dir") or (Path.home() / "Phantron" / "Files")
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # never let a write error stop the assistant
            print("[config] could not save config.json:", exc)


def load_config(path: Path = CONFIG_FILE) -> Config:
    """Load config.json merged over DEFAULTS, apply env secrets, and persist."""
    user: Dict[str, Any] = {}
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print("[config] config.json unreadable, using defaults:", exc)
            user = {}

    merged = _deep_merge(DEFAULTS, user)
    merged = _apply_env(merged)

    cfg = Config(merged, path)
    # Write back so the file always shows the full, current schema. We only
    # persist the on-disk values, not env-injected secrets, to avoid leaking
    # keys into the file.
    to_persist = _deep_merge(DEFAULTS, user)
    Config(to_persist, path).save()
    return cfg


if __name__ == "__main__":
    c = load_config()
    print(json.dumps(c.data, indent=2, ensure_ascii=False))
