# -*- coding: utf-8 -*-
"""
PHANTRON configuration.

Loads ``config.json`` (next to the repo root), deep-merges it over sensible
defaults, and writes back a complete file so P7 always has every option
visible and editable. API keys can also come from environment variables, so
nothing secret has to live in the file if you would rather not.

Zero-config friendly: if you change nothing, PHANTRON still boots and runs in
text mode. To give it a real "brain" you only need ONE of:
  ONLINE:
    * an OpenRouter API key (primary online brain) -> brain.provider="openrouter"
    * any OpenAI-compatible key (OpenAI/Groq/...)   -> brain.provider="openai"
    * an Anthropic (Claude) API key                -> brain.provider="anthropic"
  OFFLINE:
    * a running local Ollama (free, private)        -> brain.provider="ollama"
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
        # "auto" prefers an online key (OpenRouter > OpenAI > Anthropic), then
        # a live local Ollama, then "offline".
        # Explicit: "openrouter" | "openai" | "anthropic" | "ollama" | "offline"
        "provider": "auto",
        "temperature": 0.6,
        "max_tokens": 1024,
        # how many recent turns of conversation to remember
        "memory_turns": 12,
        # optional separate model for image understanding (vision). Leave empty
        # to reuse the main model. e.g. a vision model id on OpenRouter/Ollama.
        "vision_model": "",

        # ── ONLINE: OpenRouter (primary). One key, hundreds of models incl.
        # free ones. Get a key at https://openrouter.ai/keys
        # Example free models:
        #   meta-llama/llama-3.1-8b-instruct:free
        #   google/gemini-2.0-flash-exp:free
        #   deepseek/deepseek-chat
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",
            "model": "meta-llama/llama-3.1-8b-instruct:free",
        },

        # ── OFFLINE: local Ollama (free, private, no internet).
        # Install from https://ollama.com then:  ollama pull llama3.1
        "ollama": {
            "url": "http://localhost:11434",
            "model": "llama3.1",
        },

        # ── ONLINE: any other OpenAI-compatible API (OpenAI, Groq, LM Studio).
        #   OpenAI     : https://api.openai.com/v1            gpt-4o-mini
        #   Groq(free) : https://api.groq.com/openai/v1       llama-3.3-70b-versatile
        #   LM Studio  : http://localhost:1234/v1             (local, free)
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },

        # ── ONLINE: Anthropic Claude
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
        "engine": "auto",         # "auto" | "pyttsx3" | "sapi"
        "rate": 178,              # speaking speed (words/min-ish)
        "volume": 1.0,            # 0.0 - 1.0
        "vosk_model": "",         # path to a Vosk model folder for offline STT

        # Accent separation: PHANTRON detects Hindi vs English in each sentence
        # and speaks each part with its OWN voice, so accents never mix.
        # Leave a field empty to auto-pick the best matching installed voice.
        # Put part of the installed voice's name/id (e.g. "Hemant", "Kalpana",
        # "David", "Zira", "Heera", "Ravi"). See voices with: python -m phantron --voices
        "voice_hindi": "",        # voice used for Hindi (Devanagari) text
        "voice_english": "",      # voice used for English (Latin) text
        "mixed_speech": True,     # split a sentence by script and switch voices
    },

    "vision": {
        # Path to the Tesseract executable for OCR. On Windows usually:
        # C:\\Program Files\\Tesseract-OCR\\tesseract.exe
        "tesseract_cmd": "",
    },

    "agent": {
        "autonomous": True,       # let the brain decide and run tools
        "max_steps": 6,           # safety bound on autonomous tool calls
        "safe_mode": True,        # block catastrophic shell commands
        "allow_shutdown": False,  # block shutdown/restart unless True
        "command_timeout": 120,   # seconds per shell command
        "self_heal": True,        # wrap skills in a crash-net; auto-recover
    },

    "security": {
        # PHANTRON's defensive shield. All defensive only - never offensive.
        "auto_audit_on_start": False,   # run a quick security audit at launch
        "alert_on_threat": True,        # speak/surface heuristic findings
    },

    "messaging": {
        "telegram": {
            # Create a bot with @BotFather, paste the token here. Get your
            # chat_id by messaging the bot then visiting:
            #   https://api.telegram.org/bot<token>/getUpdates
            "bot_token": "",
            "chat_id": "",
        },
        "email": {
            # For Gmail: turn on 2FA, then create an "App Password" and use it
            # here (NOT your normal password). Leave blank to disable email.
            "address": "",
            "app_password": "",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "imap_host": "imap.gmail.com",
        },
    },

    "paths": {
        # leave empty to default to ~/Phantron/Projects and ~/Phantron/Files
        "projects_dir": "",
        "files_dir": "",
    },
}

# Environment variables that can fill in secrets without editing the file.
_ENV_KEYS = {
    ("brain", "openrouter", "api_key"): ["OPENROUTER_API_KEY"],
    ("brain", "openai", "api_key"): ["OPENAI_API_KEY", "GROQ_API_KEY", "PHANTRON_API_KEY"],
    ("brain", "anthropic", "api_key"): ["ANTHROPIC_API_KEY"],
    ("messaging", "telegram", "bot_token"): ["TELEGRAM_BOT_TOKEN"],
    ("messaging", "email", "app_password"): ["PHANTRON_EMAIL_PASSWORD"],
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
