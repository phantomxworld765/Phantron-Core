# -*- coding: utf-8 -*-
"""
PHANTRON vision - let the assistant SEE the screen.

Capabilities (all optional, all lazy-imported so nothing breaks if a library
is absent):

  * capture()       - grab the screen (or a region) to a PNG file
  * read_screen()   - OCR the screen to text (Tesseract via pytesseract)
  * find_text()     - locate on-screen text and return its centre coordinates
                      so the agent can click it
  * describe()      - if a vision-capable LLM is configured, send the
                      screenshot for a natural-language description

OCR needs the Tesseract engine installed on the PC:
  Windows: https://github.com/UB-Mannheim/tesseract/wiki  (then set the path
  in config: vision.tesseract_cmd) + `pip install pytesseract pillow`.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple


def _mss():
    try:
        import mss  # fast cross-platform screen capture
        return mss
    except Exception:
        return None


def _pyautogui():
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None


def _pytesseract(cfg):
    try:
        import pytesseract
        cmd = cfg.get("vision.tesseract_cmd", "")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        return pytesseract
    except Exception:
        return None


class Vision:
    def __init__(self, cfg, brain=None, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.brain = brain
        self.on_event = on_event

    def _emit(self, action: str, detail: str = "", kind: str = "vision"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    @property
    def can_capture(self) -> bool:
        return _mss() is not None or _pyautogui() is not None

    @property
    def can_ocr(self) -> bool:
        return _pytesseract(self.cfg) is not None

    # ─────────────────────────────────────────────────────────────────────
    #  Capture
    # ─────────────────────────────────────────────────────────────────────
    def capture(self, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Path]:
        """Save a screenshot and return its path (or None if unavailable)."""
        out = self.cfg.files_dir() / ("screen_%d.png" % int(time.time()))
        mss = _mss()
        if mss:
            try:
                with mss.mss() as sct:
                    if region:
                        x, y, w, h = region
                        mon = {"left": x, "top": y, "width": w, "height": h}
                    else:
                        mon = sct.monitors[1]
                    img = sct.grab(mon)
                    mss.tools.to_png(img.rgb, img.size, output=str(out))
                self._emit("Captured", str(out), "vision")
                return out
            except Exception:
                pass
        pag = _pyautogui()
        if pag:
            try:
                shot = pag.screenshot(region=region) if region else pag.screenshot()
                shot.save(str(out))
                self._emit("Captured", str(out), "vision")
                return out
            except Exception:
                pass
        return None

    # ─────────────────────────────────────────────────────────────────────
    #  OCR
    # ─────────────────────────────────────────────────────────────────────
    def read_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> str:
        pt = _pytesseract(self.cfg)
        if not pt:
            return ("OCR ke liye Tesseract + pytesseract chahiye. Install karke "
                    "config.json me vision.tesseract_cmd set kar de.")
        path = self.capture(region)
        if not path:
            return "Screen capture nahi ho saka (mss ya pyautogui install kar de)."
        try:
            from PIL import Image
            text = pt.image_to_string(Image.open(str(path)))
            text = (text or "").strip()
            self._emit("OCR", text[:80], "vision")
            return text or "Screen pe koi text nahi mila."
        except Exception as exc:
            return "OCR error: " + str(exc)

    def find_text(self, needle: str = "", region=None):
        """Return (x, y) centre of the first on-screen match of `needle`."""
        pt = _pytesseract(self.cfg)
        if not pt or not needle:
            return None
        path = self.capture(region)
        if not path:
            return None
        try:
            from PIL import Image
            data = pt.image_to_data(Image.open(str(path)), output_type=pt.Output.DICT)
            needle_low = needle.lower()
            for i, word in enumerate(data.get("text", [])):
                if word and needle_low in word.lower():
                    x = data["left"][i] + data["width"][i] // 2
                    y = data["top"][i] + data["height"][i] // 2
                    if region:
                        x += region[0]; y += region[1]
                    return (x, y)
        except Exception:
            return None
        return None

    # ─────────────────────────────────────────────────────────────────────
    #  Describe via a vision LLM (optional)
    # ─────────────────────────────────────────────────────────────────────
    def describe(self, question: str = "What is on the screen?") -> str:
        path = self.capture()
        if not path:
            return "Screen capture nahi ho saka."
        if not self.brain or not getattr(self.brain, "online", False):
            # fall back to OCR text if we can
            if self.can_ocr:
                txt = self.read_screen()
                return "Screen pe ye text dikha:\n" + txt[:800]
            return "Screen describe karne ke liye ek vision model (config me) chahiye."
        try:
            b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            return self.brain.see(question, b64)
        except Exception as exc:
            return "Vision error: " + str(exc)
