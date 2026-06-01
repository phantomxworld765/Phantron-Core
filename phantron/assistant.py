# -*- coding: utf-8 -*-
"""
PHANTRON assistant - the orchestrator that wires everything together.

It owns the brain, the skills, the agent loop and (optionally) the voice
engine, and exposes a single ``chat(text)`` method that the web server and
the voice loop both call. Event callbacks let the UI show what PHANTRON is
thinking and doing in real time.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from .agent import Agent
from .brain import Brain
from .config import Config, load_config
from .memory import Memory
from .messaging import Messaging
from .security import Security
from .selfheal import SelfHeal
from .skills import Skills
from .vision import Vision
from .voice import Voice


class Assistant:
    def __init__(self, cfg: Optional[Config] = None,
                 on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg or load_config()
        self._external_event = on_event
        self._listeners: List[Callable[[dict], None]] = []

        self.brain = Brain(self.cfg)
        self.memory = Memory(self.cfg)
        self.vision = Vision(self.cfg, brain=self.brain, on_event=self._event)
        self.security = Security(self.cfg, on_event=self._event)
        self.messaging = Messaging(self.cfg, on_event=self._event)
        self.selfheal = SelfHeal(self.cfg, brain=self.brain, on_event=self._event)
        self.skills = Skills(self.cfg, on_event=self._event,
                             vision=self.vision, memory=self.memory,
                             notify=self._notify,
                             security=self.security, messaging=self.messaging,
                             selfheal=self.selfheal)
        self.agent = Agent(self.cfg, self.brain, self.skills,
                           on_event=self._event, memory=self.memory)
        self.voice = Voice(self.cfg, on_event=self._event)

        self._voice_thread: Optional[threading.Thread] = None
        self._voice_stop = threading.Event()

        # Re-arm reminders saved from previous sessions.
        try:
            restored = self.skills.reschedule_pending()
            if restored:
                self._event("Reminders", "%d pending restored" % restored, "info")
        except Exception:
            pass

        # Optional: a quick security audit at launch.
        if self.cfg.get("security.auto_audit_on_start", False):
            threading.Thread(target=self._startup_audit, daemon=True).start()

    def _startup_audit(self):
        try:
            report = self.security.audit()
            self._event("Security", report.splitlines()[0], "security")
        except Exception:
            pass

    def _notify(self, text: str):
        """Called by reminders/timers: surface to the UI and speak it aloud."""
        self._event("PHANTRON", text, "reply")
        if self.voice.can_speak:
            threading.Thread(target=self.voice.speak, args=(text,), daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────
    #  Event plumbing (for the UI)
    # ─────────────────────────────────────────────────────────────────────
    def add_listener(self, fn: Callable[[dict], None]):
        self._listeners.append(fn)

    def _event(self, action: str, detail: str = "", kind: str = "info"):
        payload = {"action": action, "detail": detail, "kind": kind}
        for fn in list(self._listeners):
            try:
                fn(payload)
            except Exception:
                pass
        if self._external_event:
            try:
                self._external_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  Core
    # ─────────────────────────────────────────────────────────────────────
    def chat(self, text: str, speak: bool = False) -> str:
        self.memory.add_journal("P7", text)
        # run the agent inside the self-heal crash-net when enabled, so one
        # broken skill never takes the whole assistant down.
        if self.cfg.get("agent.self_heal", True):
            reply = self.selfheal.guard("chat", self.agent.handle, text)
        else:
            reply = self.agent.handle(text)
        self.memory.add_journal("PHANTRON", reply)
        if speak and self.voice.can_speak and reply:
            threading.Thread(target=self.voice.speak, args=(reply,), daemon=True).start()
        return reply

    def status(self) -> dict:
        b = self.brain.status()
        return {
            "assistant_name": self.cfg.get("assistant_name", "PHANTRON"),
            "user_name": self.cfg.get("user_name", "P7"),
            "version": __import__("phantron").__version__,
            "brain": b,
            "voice": self.voice.status(),
            "vision": {
                "capture": self.vision.can_capture,
                "ocr": self.vision.can_ocr,
            },
            "security": self.security.status(),
            "messaging": self.messaging.status(),
            "selfheal": self.selfheal.status(),
            "memory_facts": len(self.memory.facts),
            "autonomous": bool(self.cfg.get("agent.autonomous", True)),
        }

    def greeting(self) -> str:
        b = self.brain.status()
        name = self.cfg.get("assistant_name", "PHANTRON")
        user = self.cfg.get("user_name", "P7")
        if b["provider"] == "offline":
            return ("%s online, %s. Brain abhi connected nahi - command mode me hoon. "
                    "Ollama ya API key laga do toh main poori tarah soch paunga." % (name, user))
        return "%s online, %s. Brain ready (%s). Bolo kya karna hai?" % (name, user, b["model"])

    # ─────────────────────────────────────────────────────────────────────
    #  Voice loop (wake-word -> listen -> act -> speak)
    # ─────────────────────────────────────────────────────────────────────
    def start_voice_loop(self):
        if not self.voice.can_listen:
            return False
        if self._voice_thread and self._voice_thread.is_alive():
            return True
        self._voice_stop.clear()
        self._voice_thread = threading.Thread(target=self._voice_loop, daemon=True)
        self._voice_thread.start()
        return True

    def stop_voice_loop(self):
        self._voice_stop.set()

    def _voice_loop(self):
        wake = (self.cfg.get("voice.wake_word", "phantron") or "phantron").lower()
        self._event("Voice", "Listening for wake word '%s'" % wake, "listen")
        while not self._voice_stop.is_set():
            heard = self.voice.listen_once(timeout=5, phrase_limit=6)
            if not heard:
                continue
            low = heard.lower()
            if wake in low:
                # remove the wake word; if a command followed, use it directly
                command = low.split(wake, 1)[1].strip(" ,.!")
                if not command:
                    self.voice.speak("Haan, " + self.cfg.get("user_name", "P7"))
                    command = self.voice.listen_once(timeout=6, phrase_limit=8) or ""
                if command:
                    reply = self.chat(command, speak=True)
                    self._event("Replied", reply[:80], "speak")
