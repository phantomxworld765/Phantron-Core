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
        self.reminders_path = base / "reminders.json"
        self._lock = threading.Lock()
        self.facts: Dict[str, str] = self._load(self.facts_path, {})
        self.journal: List[dict] = self._load(self.journal_path, [])
        self.reminders: List[dict] = self._load(self.reminders_path, [])

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

    # ---- reminders (persisted so they survive restarts) ----
    def add_reminder(self, text: str, fire_at: float) -> dict:
        """Store a reminder with an absolute fire time (epoch seconds)."""
        with self._lock:
            item = {"id": int(time.time() * 1000), "text": text, "fire_at": fire_at,
                    "done": False}
            self.reminders.append(item)
            self._save(self.reminders_path, self.reminders)
            return item

    def mark_reminder_done(self, reminder_id: int):
        with self._lock:
            changed = False
            for r in self.reminders:
                if r.get("id") == reminder_id and not r.get("done"):
                    r["done"] = True
                    changed = True
            # keep only the last 100 (done or not) to avoid unbounded growth
            self.reminders = self.reminders[-100:]
            if changed:
                self._save(self.reminders_path, self.reminders)

    def pending_reminders(self) -> List[dict]:
        """Reminders that have not fired yet."""
        with self._lock:
            return [dict(r) for r in self.reminders if not r.get("done")]

    def list_reminders(self) -> str:
        pend = self.pending_reminders()
        if not pend:
            return "Koi pending reminder nahi hai, P7."
        from datetime import datetime
        lines = []
        for r in sorted(pend, key=lambda x: x.get("fire_at", 0)):
            when = datetime.fromtimestamp(r["fire_at"]).strftime("%d %b %I:%M %p")
            lines.append("- %s @ %s" % (r["text"], when))
        return "Pending reminders:\n" + "\n".join(lines)

    # ---- conversation export ----
    def export_conversation(self, fmt: str = "txt") -> Path:
        """Write the full journal to a timestamped file; return its path."""
        from datetime import datetime
        with self._lock:
            data = list(self.journal)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        files_dir = self.cfg.files_dir()
        if fmt == "json":
            out = files_dir / ("conversation_%s.json" % stamp)
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            out = files_dir / ("conversation_%s.txt" % stamp)
            lines = []
            for e in data:
                ts = datetime.fromtimestamp(e.get("t", 0)).strftime("%Y-%m-%d %H:%M")
                lines.append("[%s] %s: %s" % (ts, e.get("role", "?"), e.get("text", "")))
            out.write_text("\n".join(lines), encoding="utf-8")
        return out
