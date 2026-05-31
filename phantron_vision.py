# -*- coding: utf-8 -*-
"""
PHANTRON VISION  -  the "eyes" of the agent.

Gives PHANTRON Computer-Vision perception of the live screen so it can:
  * read on-screen text          (OCR via pytesseract, optional)
  * locate text and return click coordinates
  * locate an image/template on screen (pyautogui / OpenCV, optional)
  * describe what is currently visible
  * detect when the screen has CHANGED (cheap perceptual signature)

Design rule (same as the rest of PHANTRON): never crash if an optional
library is missing. Every capability degrades to a clear, actionable message
so the agent always knows what it can and cannot see.

Optional libraries (install only what you need):
    pip install pyautogui Pillow      # capture + image template matching
    pip install pytesseract           # OCR text reading  (also needs the
                                       # Tesseract-OCR engine on the system)
    pip install opencv-python numpy   # robust template matching + diffing
"""

import time
from io import BytesIO


# ── optional libs, loaded lazily and safely ────────────────────────────────
def _pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except Exception:
        return None


def _pil():
    try:
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _tesseract():
    try:
        import pytesseract
        return pytesseract
    except Exception:
        return None


def _cv2():
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
        return True
    except Exception:
        return False


class VisionEngine:
    """Stateful screen-perception engine. One instance is enough."""

    def __init__(self, config=None):
        self.config = config or {}
        self._last_sig = None

    # ── capability report ──────────────────────────────────────────────
    def capabilities(self):
        return {
            "capture": _pyautogui() is not None and _pil(),
            "ocr": _tesseract() is not None,
            "template_match": (_pyautogui() is not None) or _cv2(),
        }

    def available(self):
        c = self.capabilities()
        return c["capture"]

    # ── raw capture ────────────────────────────────────────────────────
    def capture(self, region=None):
        """Return a PIL screenshot (optionally a (x, y, w, h) region) or None."""
        pg = _pyautogui()
        if not pg:
            return None
        try:
            if region:
                x, y, w, h = region
                return pg.screenshot(region=(int(x), int(y), int(w), int(h)))
            return pg.screenshot()
        except Exception:
            return None

    def screen_size(self):
        pg = _pyautogui()
        try:
            return tuple(pg.size()) if pg else (0, 0)
        except Exception:
            return (0, 0)

    # ── OCR: read text on screen ───────────────────────────────────────
    def read_text(self, region=None):
        """Return all readable on-screen text as a single string."""
        ts = _tesseract()
        if not ts:
            return "__NO_OCR__"
        img = self.capture(region)
        if img is None:
            return "__NO_CAPTURE__"
        try:
            return (ts.image_to_string(img) or "").strip()
        except Exception as e:
            return "__OCR_ERR__ " + str(e)[:80]

    def find_text(self, query, region=None, limit=8):
        """Find `query` on screen. Returns a list of hits:
        [{"text", "x", "y", "w", "h", "cx", "cy", "conf"}], where (cx, cy) is
        the absolute screen coordinate to click. Empty list if not found."""
        ts = _tesseract()
        if not ts:
            return []
        img = self.capture(region)
        if img is None:
            return []
        ox, oy = (int(region[0]), int(region[1])) if region else (0, 0)
        q = (query or "").strip().lower()
        if not q:
            return []
        try:
            from pytesseract import Output
            data = ts.image_to_data(img, output_type=Output.DICT)
        except Exception:
            return []

        # group words into lines so multi-word queries can match a phrase
        lines = {}
        n = len(data.get("text", []))
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append({
                "text": word,
                "left": data["left"][i], "top": data["top"][i],
                "width": data["width"][i], "height": data["height"][i],
                "conf": _safe_int(data.get("conf", [0] * n)[i]),
            })

        hits = []
        for words in lines.values():
            line_text = " ".join(w["text"] for w in words)
            if q in line_text.lower():
                # bounding box of the matched words on this line
                matched = [w for w in words if any(part in w["text"].lower()
                                                   for part in q.split())] or words
                left = min(w["left"] for w in matched)
                top = min(w["top"] for w in matched)
                right = max(w["left"] + w["width"] for w in matched)
                bottom = max(w["top"] + w["height"] for w in matched)
                conf = max((w["conf"] for w in matched), default=0)
                hits.append({
                    "text": line_text,
                    "x": left + ox, "y": top + oy,
                    "w": right - left, "h": bottom - top,
                    "cx": ox + (left + right) // 2,
                    "cy": oy + (top + bottom) // 2,
                    "conf": conf,
                })
        hits.sort(key=lambda h: h["conf"], reverse=True)
        return hits[:limit]

    # ── template / image matching ──────────────────────────────────────
    def locate_image(self, template_path, confidence=0.8, region=None):
        """Return the (cx, cy) center of `template_path` on screen, or None."""
        pg = _pyautogui()
        if not pg:
            return None
        try:
            kw = {}
            if region:
                kw["region"] = tuple(int(v) for v in region)
            # confidence needs opencv; fall back without it
            try:
                pt = pg.locateCenterOnScreen(template_path, confidence=confidence, **kw)
            except TypeError:
                pt = pg.locateCenterOnScreen(template_path, **kw)
            return (int(pt[0]), int(pt[1])) if pt else None
        except Exception:
            return None

    # ── describe the current screen ────────────────────────────────────
    def describe(self, ai_fn=None, max_chars=1200):
        """Return a short, human description of what is on screen right now.
        If `ai_fn(prompt, system)` is given and OCR text is available, the AI
        summarizes it; otherwise we return the raw OCR text / a status note."""
        if not self.available():
            return ("Screen dekhne ke liye 'pip install pyautogui Pillow' chahiye. "
                    "Abhi vision off hai.")
        title = self.active_window_title()
        text = self.read_text()
        if text == "__NO_OCR__":
            base = "Screen capture ho gaya"
            if title:
                base += ", active window: '%s'" % title
            return (base + ". Text padhne (OCR) ke liye 'pip install pytesseract' "
                    "aur Tesseract-OCR engine install karo.")
        if text.startswith("__"):
            return "Screen padhne me dikkat: " + text
        snippet = " ".join(text.split())[:max_chars]
        if ai_fn and snippet:
            try:
                sys_p = ("Tum ek screen-reader ho. Neeche screen ka OCR text hai. "
                         "2-3 line me Hindi me batao screen par kya khula/dikh raha hai. "
                         "Sirf summary, koi emoji nahi.")
                out = ai_fn("Active window: %s\n\nOCR text:\n%s" % (title or "?", snippet), sys_p)
                if out and not str(out).startswith("__ERR__"):
                    return str(out).strip()
            except Exception:
                pass
        head = ("Active window: '%s'. " % title) if title else ""
        return head + ("Screen par ye text dikh raha hai: " + snippet if snippet
                       else "Screen par koi readable text nahi mila.")

    def active_window_title(self):
        pg = _pyautogui()
        try:
            if pg and hasattr(pg, "getActiveWindow"):
                w = pg.getActiveWindow()
                if w and getattr(w, "title", None):
                    return w.title
        except Exception:
            pass
        return ""

    # ── change detection (cheap perceptual signature) ──────────────────
    def screen_signature(self):
        """A tiny grayscale fingerprint of the screen for change detection."""
        img = self.capture()
        if img is None:
            return None
        try:
            small = img.convert("L").resize((16, 16))
            return tuple(small.getdata())
        except Exception:
            return None

    def has_changed(self, threshold=8):
        """True if the screen changed meaningfully since the last call."""
        sig = self.screen_signature()
        if sig is None:
            return False
        if self._last_sig is None:
            self._last_sig = sig
            return True
        diff = sum(1 for a, b in zip(sig, self._last_sig) if abs(a - b) > 18)
        self._last_sig = sig
        return diff >= threshold

    def wait_for_change(self, timeout=8.0, poll=0.5):
        """Block until the screen changes or timeout. Returns True if changed."""
        self.has_changed()  # prime baseline
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(poll)
            if self.has_changed():
                return True
        return False


def _safe_int(v):
    try:
        return int(float(v))
    except Exception:
        return 0


# convenience singleton + module-level helpers
_ENGINE = None


def engine(config=None):
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = VisionEngine(config)
    return _ENGINE


if __name__ == "__main__":
    e = VisionEngine()
    print("Vision capabilities:", e.capabilities())
    print("Screen size       :", e.screen_size())
    print("Describe           :", e.describe()[:200])
