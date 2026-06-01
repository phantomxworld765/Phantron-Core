# -*- coding: utf-8 -*-
"""
PHANTRON voice - advanced speech I/O with CLEAN Hindi/English separation.

The key design goal (P7's requirement): Hindi and English accents must NOT
mix. So before speaking, PHANTRON splits the text into runs of the same
script - Devanagari vs Latin - and speaks each run with its OWN voice:

    "Theek hai, opening Chrome अभी"
      -> "Theek hai, opening Chrome"   spoken by the English voice
      -> "अभी"                          spoken by the Hindi voice

Each voice can be chosen explicitly in config (voice.voice_hindi /
voice.voice_english) or auto-picked from installed voices. If only one
suitable voice exists, that one is used for everything.

Everything is optional and lazy: with no speech libraries installed, Voice
reports itself unavailable and PHANTRON keeps working in text mode.

TTS : pyttsx3 (offline, cross-platform) preferred; Windows SAPI fallback.
STT : Vosk (offline, if voice.vosk_model set) preferred; else free Google web.
"""

from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional, Tuple


# Name/id hints for auto-picking voices when not set explicitly in config.
_HINDI_HINTS = ["hindi", "hi-in", "hi_in", "hemant", "kalpana", "swara", "madhur", "devanagari"]
_ENGLISH_HINTS = ["english", "en-us", "en_us", "en-gb", "en-in", "david", "zira",
                  "mark", "hazel", "ravi", "heera", "aria", "guy", "jenny"]

# Devanagari Unicode block (Hindi/Marathi/etc.)
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _is_devanagari(ch: str) -> bool:
    return bool(_DEVANAGARI.match(ch))


def split_by_script(text: str) -> List[Tuple[str, str]]:
    """
    Split text into ordered (lang, chunk) pairs where lang is "hi" or "en".
    Whitespace and punctuation stick to the surrounding run so we don't make
    tiny fragments. Returns e.g. [("en","opening chrome "), ("hi","अभी")].
    """
    if not text:
        return []
    segments: List[Tuple[str, str]] = []
    cur_lang: Optional[str] = None
    buf: List[str] = []

    for ch in text:
        if _is_devanagari(ch):
            lang = "hi"
        elif ch.isalpha():
            lang = "en"
        else:
            # neutral char (space/digit/punct) - attach to current run
            if buf:
                buf.append(ch)
            else:
                buf.append(ch)
                cur_lang = cur_lang or "en"
            continue
        if cur_lang is None:
            cur_lang = lang
            buf.append(ch)
        elif lang == cur_lang:
            buf.append(ch)
        else:
            segments.append((cur_lang, "".join(buf)))
            cur_lang = lang
            buf = [ch]

    if buf:
        segments.append((cur_lang or "en", "".join(buf)))

    # merge adjacent same-language runs and drop empty/whitespace-only ones
    merged: List[Tuple[str, str]] = []
    for lang, chunk in segments:
        if not chunk.strip():
            if merged:
                merged[-1] = (merged[-1][0], merged[-1][1] + chunk)
            continue
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + chunk)
        else:
            merged.append((lang, chunk))
    return merged


class Voice:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self._tts = None
        self._tts_kind = None
        self._voice_hi: Optional[str] = None   # resolved voice id for Hindi
        self._voice_en: Optional[str] = None   # resolved voice id for English
        self._recognizer = None
        self._mic = None
        self._vosk = None
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
                self._tts = engine
                self._tts_kind = "pyttsx3"
                self._resolve_voices(engine)
                return
            except Exception:
                pass

        # Windows SAPI fallback (single voice, no per-language switching)
        try:
            import platform
            if platform.system() == "Windows":
                import win32com.client  # type: ignore
                self._tts = win32com.client.Dispatch("SAPI.SpVoice")
                self._tts_kind = "sapi"
        except Exception:
            self._tts = None
            self._tts_kind = None

    def _resolve_voices(self, engine):
        """Decide which installed voice to use for Hindi and for English."""
        try:
            voices = engine.getProperty("voices")
        except Exception:
            return

        def find(pref_id: str, hints: List[str]) -> Optional[str]:
            pref_id = (pref_id or "").strip().lower()
            # explicit preference by name/id substring
            if pref_id:
                for v in voices:
                    blob = ((v.id or "") + " " + (getattr(v, "name", "") or "")).lower()
                    if pref_id in blob:
                        return v.id
            # auto by language hints
            for v in voices:
                blob = ((v.id or "") + " " + (getattr(v, "name", "") or "")).lower()
                langs = ""
                try:
                    langs = " ".join(str(x) for x in (getattr(v, "languages", []) or [])).lower()
                except Exception:
                    pass
                if any(h in blob or h in langs for h in hints):
                    return v.id
            return None

        self._voice_hi = find(self.cfg.get("voice.voice_hindi", ""), _HINDI_HINTS)
        self._voice_en = find(self.cfg.get("voice.voice_english", ""), _ENGLISH_HINTS)

        # graceful fallbacks: if one is missing, reuse the other or the default
        default_id = voices[0].id if voices else None
        if not self._voice_en:
            self._voice_en = self._voice_hi or default_id
        if not self._voice_hi:
            self._voice_hi = self._voice_en or default_id

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
                    self._speak_pyttsx3(text)
                elif self._tts_kind == "sapi":
                    self._tts.Speak(text)
            except Exception as exc:
                self._emit("TTS error", str(exc)[:60], "error")

    def _speak_pyttsx3(self, text: str):
        """Speak with per-script voice switching so accents never mix."""
        mixed = bool(self.cfg.get("voice.mixed_speech", True))
        # If switching is off, or we only resolved a single voice, speak plainly.
        if not mixed or (self._voice_hi == self._voice_en):
            if self._voice_en:
                try:
                    self._tts.setProperty("voice", self._voice_en)
                except Exception:
                    pass
            self._tts.say(text)
            self._tts.runAndWait()
            return

        for lang, chunk in split_by_script(text):
            if not chunk.strip():
                continue
            vid = self._voice_hi if lang == "hi" else self._voice_en
            if vid:
                try:
                    self._tts.setProperty("voice", vid)
                except Exception:
                    pass
            self._tts.say(chunk)
            self._tts.runAndWait()

    # ─────────────────────────────────────────────────────────────────────
    #  Speech to text
    # ─────────────────────────────────────────────────────────────────────
    def _init_stt(self):
        if not self.cfg.get("voice.input", True):
            return
        model_path = self.cfg.get("voice.vosk_model", "")
        if model_path:
            try:
                import os
                from vosk import Model, KaldiRecognizer  # noqa
                if os.path.isdir(model_path):
                    self._vosk = ("vosk", model_path)
            except Exception:
                self._vosk = None

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
        if not self.can_listen:
            return None
        try:
            with self._mic as source:
                audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
        except Exception:
            return None

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
        return {
            "tts": self._tts_kind or "none",
            "stt": stt,
            "voice_hi": self._voice_hi or "-",
            "voice_en": self._voice_en or "-",
        }
