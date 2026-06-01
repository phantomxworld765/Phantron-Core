# -*- coding: utf-8 -*-
"""
PHANTRON voice - optional speech input/output.

Everything here is best-effort and lazy: if the speech libraries are not
installed, Voice simply reports that it is unavailable and PHANTRON keeps
working in text mode. Nothing in this module is imported at package load
time, so a machine without a microphone or TTS engine still runs fine.

Speech-to-text : SpeechRecognition + PyAudio (Google web STT, free)
Text-to-speech : pyttsx3 (offline, cross-platform) preferred; falls back to
                 SAPI on Windows if pyttsx3 is missing.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class Voice:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self._tts = None
        self._tts_kind = None
        self._recognizer = None
        self._mic = None
        self._speak_lock = threading.Lock()
        self._init_tts()
        self._init_stt()

    def _emit(self, action: str, detail: str = "", kind: str = "voice"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  Text to speech
    # ─────────────────────────────────────────────────────────────────────
    def _init_tts(self):
        if not self.cfg.get("voice.output", True):
            return
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 178)
            voice_id = self.cfg.get("voice.tts_voice", "")
            if voice_id:
                try:
                    engine.setProperty("voice", voice_id)
                except Exception:
                    pass
            self._tts = engine
            self._tts_kind = "pyttsx3"
            return
        except Exception:
            pass
        # Windows SAPI fallback
        try:
            import platform
            if platform.system() == "Windows":
                import win32com.client  # type: ignore
                self._tts = win32com.client.Dispatch("SAPI.SpVoice")
                self._tts_kind = "sapi"
        except Exception:
            self._tts = None
            self._tts_kind = None

    @property
    def can_speak(self) -> bool:
        return self._tts is not None

    @property
    def can_listen(self) -> bool:
        return self._recognizer is not None and self._mic is not None

    def speak(self, text: str):
        text = (text or "").strip()
        if not text or not self._tts:
            return
        with self._speak_lock:
            try:
                self._emit("Speaking", text[:60], "speak")
                if self._tts_kind == "pyttsx3":
                    self._tts.say(text)
                    self._tts.runAndWait()
                elif self._tts_kind == "sapi":
                    self._tts.Speak(text)
            except Exception as exc:
                self._emit("TTS error", str(exc)[:60], "error")

    # ─────────────────────────────────────────────────────────────────────
    #  Speech to text
    # ─────────────────────────────────────────────────────────────────────
    def _init_stt(self):
        if not self.cfg.get("voice.input", True):
            return
        try:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._recognizer.dynamic_energy_threshold = True
            self._mic = sr.Microphone()
            with self._mic as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.6)
        except Exception:
            self._recognizer = None
            self._mic = None

    def listen_once(self, timeout: float = 6.0, phrase_limit: float = 8.0) -> Optional[str]:
        """Block until one phrase is heard; return recognized text or None."""
        if not self.can_listen:
            return None
        try:
            import speech_recognition as sr
            with self._mic as source:
                audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            lang = self.cfg.get("voice.stt_language", "en-IN")
            text = self._recognizer.recognize_google(audio, language=lang)
            if text:
                self._emit("Heard", text, "listen")
            return text
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except Exception as exc:
            self._emit("STT error", str(exc)[:60], "error")
            return None

    def status(self) -> dict:
        return {
            "tts": self._tts_kind or "none",
            "stt": "google" if self.can_listen else "none",
        }
