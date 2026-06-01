# -*- coding: utf-8 -*-
"""
PHANTRON memory - persistent, cross-session memory (stdlib only).

Two layers, both stored as JSON under the user's Phantron folder:

  * facts   : durable key/value things PHANTRON should always remember about
              P7 (name spelling, preferences, "my laptop is a Dell", ...).
  * journal : a rolling log of recent conversation summaries so PHANTRON has
              continuity between runs without resending the whole history.

Nothing here needs a database or any package - just files. It is safe to use
even if the files do not exist yet; they are created on first write.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, List


class Memory:
    def __init__(self, cfg):
        self.cfg = cfg
        base = cfg.files_dir().parent / "memory"
        base.mkdir(parents=True, exist_ok=True)
        self.facts_path = base / "facts.json"
        self.journal_path = base / "journal.json"
        self._lock = threading.Lock()
        self.facts: Dict[str, str] = self._load(self.facts_path, {})
        self.journal: List[dict] = self._load(self.journal_path, [])

    # ---- io ----
    def _load(self, path: Path, default):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    def _save(self, path: Path, data):
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ---- facts ----
    def remember(self, key: str, value: str) -> str:
        key = (key or "").strip().lower()
        if not key:
            return "Kya yaad rakhun?"
        with self._lock:
            self.facts[key] = (value or "").strip()
            self._save(self.facts_path, self.facts)
        return "Yaad rakh liya, P7: %s = %s" % (key, value)

    def recall(self, key: str = "") -> str:
        key = (key or "").strip().lower()
        with self._lock:
            if not key:
                if not self.facts:
                    return "Abhi kuch yaad nahi hai."
                return "; ".join("%s: %s" % (k, v) for k, v in self.facts.items())
            # exact hit
            if key in self.facts:
                return self.facts[key]
            # fuzzy: any stored key that contains the query (or vice-versa)
            for k, v in self.facts.items():
                if key in k or k in key:
                    return v
            # word-overlap fallback
            qwords = set(key.split())
            for k, v in self.facts.items():
                if qwords & set(k.split()):
                    return v
            return "Iske baare me kuch yaad nahi."

    def forget(self, key: str) -> str:
        key = (key or "").strip().lower()
        with self._lock:
            if key in self.facts:
                del self.facts[key]
                self._save(self.facts_path, self.facts)
                return "Bhula diya: " + key
        return "Aisa kuch yaad nahi tha."

    # ---- journal ----
    def add_journal(self, role: str, text: str):
        with self._lock:
            self.journal.append({"t": time.time(), "role": role, "text": text[:500]})
            if len(self.journal) > 200:
                self.journal = self.journal[-200:]
            self._save(self.journal_path, self.journal)

    def recent_context(self, limit: int = 6) -> str:
        """A compact recap of the last few exchanges for the system prompt."""
        with self._lock:
            tail = self.journal[-limit:]
        if not tail:
            return ""
        lines = ["%s: %s" % (e["role"], e["text"]) for e in tail]
        return "\n".join(lines)

    def facts_block(self) -> str:
        with self._lock:
            if not self.facts:
                return ""
        return "; ".join("%s=%s" % (k, v) for k, v in self.facts.items())
