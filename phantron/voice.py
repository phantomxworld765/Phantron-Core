# -*- coding: utf-8 -*-
"""
PHANTRON voice - advanced, optional speech input/output.

Best-effort and lazy: if speech libraries are missing, Voice reports itself
unavailable and PHANTRON keeps working in text mode. Nothing is imported at
package load time.

Text-to-speech (in priority order):
  1. pyttsx3   - offline, cross-platform, picks a Hindi/Indian-English voice
                 automatically when available; rate/volume configurable.
  2. SAPI      - native Windows fallback.

Speech-to-text:
  * Vosk       - fully offline, accurate, if a model folder is configured
                 (voice.vosk_model). No internet needed.
  * Google web - SpeechRecognition's free recognizer (needs internet).
  Whichever is available is used; Vosk is preferred when present.

Tunables (config.json -> voice):
  output, input, wake_word, stt_language, tts_voice, rate, volume,
  engine ("auto"|"pyttsx3"|"sapi"), vosk_model
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional


# Hints used to auto-pick a pleasant Indian / Hindi voice when present.
_VOICE_HINTS = ["hindi", "india", "ravi", "heera", "neerja", "swara", "kalpana", "en-in"]


class Voice:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self._tts = None
        self._tts_kind = None
        self._recognizer = None
        self._mic = None
        self._vosk = None            # (model, KaldiRecognizer-capable) when offline STT ready
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
        want = (self.cfg.get("voice.engine", "auto") or "auto").lower()

        if want in ("auto", "pyttsx3"):
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", int(self.cfg.get("voice.rate", 178)))
                engine.setProperty("volume", float(self.cfg.get("voice.volume", 1.0)))
                self._select_voice(engine)
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

    def _select_voice(self, engine):
        """Pick the configured voice id, else a Hindi/Indian voice, else default."""
        want_id = self.cfg.get("voice.tts_voice", "")
        try:
            voices = engine.getProperty("voices")
        except Exception:
            return
        # explicit id / name match
        if want_id:
            for v in voices:
                if want_id.lower() in (v.id or "").lower() or want_id.lower() in (getattr(v, "name", "") or "").lower():
                    engine.setProperty("voice", v.id)
                    return
        # auto Indian/Hindi preference
        for v in voices:
            blob = ((v.id or "") + " " + (getattr(v, "name", "") or "")).lower()
            if any(h in blob for h in _VOICE_HINTS):
                engine.setProperty("voice", v.id)
                return

    def list_voices(self) -> List[dict]:
        if self._tts_kind != "pyttsx3" or not self._tts:
            return []
        try:
            return [{"id": v.id, "name": getattr(v, "name", "")}
                    for v in self._tts.getProperty("voices")]
        except Exception:
            return []

    @property
    def can_speak(self) -> bool:
        return self._tts is not None

    @property
    def can_listen(self) -> bool:
        return (self._recognizer is not None and self._mic is not None) or self._vosk is not None

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
        # Prefer offline Vosk if a model is configured and present.
        model_path = self.cfg.get("voice.vosk_model", "")
        if model_path:
            try:
                import os
                from vosk import Model, KaldiRecognizer  # noqa
                if os.path.isdir(model_path):
                    self._vosk = ("vosk", model_path)
            except Exception:
                self._vosk = None

        # Microphone via SpeechRecognition (used by both Google STT and as a
        # mic source). If PyAudio is missing this stays unavailable.
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
        except Exception:
            return None

        # Offline first (Vosk), then Google web STT.
        if self._vosk:
            text = self._vosk_transcribe(audio)
            if text:
                self._emit("Heard", text, "listen")
                return text
        try:
            lang = self.cfg.get("voice.stt_language", "en-IN")
            text = self._recognizer.recognize_google(audio, language=lang)
            if text:
                self._emit("Heard", text, "listen")
            return text
        except Exception:
            return None

    def _vosk_transcribe(self, audio) -> Optional[str]:
        try:
            import json
            from vosk import Model, KaldiRecognizer
            if not isinstance(self._vosk, tuple):
                return None
            model = Model(self._vosk[1])
            rec = KaldiRecognizer(model, audio.sample_rate)
            rec.AcceptWaveform(audio.get_raw_data(convert_rate=audio.sample_rate, convert_width=2))
            res = json.loads(rec.FinalResult())
            return (res.get("text") or "").strip() or None
        except Exception:
            return None

    def status(self) -> dict:
        stt = "none"
        if self._vosk:
            stt = "vosk(offline)"
        elif self._recognizer and self._mic:
            stt = "google"
        return {"tts": self._tts_kind or "none", "stt": stt}
