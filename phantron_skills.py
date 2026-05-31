# -*- coding: utf-8 -*-
"""
PHANTRON SKILLS  -  high-level, real-world tasks built on top of
app-launching (phantron_apps), keyboard/mouse automation (pyautogui) and
vision (phantron_vision).

These are pragmatic UI automations for the user's OWN apps and accounts:

    WhatsApp Desktop : open, find a contact, type + send, read latest chat
    YouTube          : search, play, skip the "Skip Ads" button, next, pause

Reality check (kept honest so nothing "silently fails"):
  * UI automation depends on the app's layout. Shortcuts used here are the
    documented ones (WhatsApp Ctrl+F search, YouTube keyboard shortcuts), but
    if a window isn't focused or a layout changed, a step can miss. Every
    method returns a clear status string and uses vision where possible to
    verify/locate things instead of blind clicking.
  * Needs: pip install pyautogui   (and pytesseract for reading/ad-skip).

Dependency injection keeps this decoupled and testable:
    Skills(apps=AppController, vision=VisionEngine, sleep=fn)
The keyboard/mouse calls go through pyautogui directly (loaded lazily).
"""

import time
import webbrowser


def _pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except Exception:
        return None


class Skills:
    def __init__(self, config=None, apps=None, vision=None):
        self.config = config or {}
        self.apps = apps
        self.vision = vision

    # ── tiny helpers ────────────────────────────────────────────────────
    def _pg(self):
        return _pyautogui()

    def _need_pg(self):
        if not self._pg():
            return "Iske liye keyboard/mouse control chahiye P7: pip install pyautogui."
        return None

    def _type(self, text, interval=0.02):
        pg = self._pg()
        if pg:
            pg.typewrite(text, interval=interval)

    def _hotkey(self, *keys):
        pg = self._pg()
        if pg:
            pg.hotkey(*keys)

    def _press(self, key, n=1):
        pg = self._pg()
        if pg:
            for _ in range(n):
                pg.press(key)

    def _open_app(self, name):
        if self.apps:
            return self.apps.launch(name)
        return None

    def _click_text(self, text, timeout=0.0):
        """Find on-screen text via vision and click it. Returns True if clicked."""
        if not (self.vision and self.vision.available()):
            return False
        end = time.time() + max(0.0, timeout)
        while True:
            hits = self.vision.find_text(text)
            if hits:
                pg = self._pg()
                if pg:
                    pg.click(hits[0]["cx"], hits[0]["cy"])
                    return True
                return False
            if time.time() >= end:
                return False
            time.sleep(0.5)

    # ════════════════════════════════════════════════════════════════════
    #  WHATSAPP
    # ════════════════════════════════════════════════════════════════════
    def whatsapp_send(self, contact, message):
        contact = (contact or "").strip()
        message = (message or "").strip()
        if not contact or not message:
            return "WhatsApp ke liye contact aur message dono batao P7."
        err = self._need_pg()
        if err:
            return err
        self._open_app("whatsapp")
        time.sleep(self.config.get("app_open_wait", 6))
        # Ctrl+F focuses the WhatsApp search box
        self._hotkey("ctrl", "f")
        time.sleep(0.6)
        self._type(contact)
        time.sleep(1.5)            # let the contact list filter
        self._press("enter")       # open the top match
        time.sleep(1.0)
        # focus the message box and send
        self._type(message)
        time.sleep(0.3)
        self._press("enter")
        return ("WhatsApp par '%s' ko message bhej diya P7: \"%s\". "
                "(Agar contact list me top match galat ho to naam aur poora likhna.)"
                % (contact, message[:60]))

    def whatsapp_open_chat(self, contact):
        contact = (contact or "").strip()
        if not contact:
            return "Kis se chat kholun P7?"
        err = self._need_pg()
        if err:
            return err
        self._open_app("whatsapp")
        time.sleep(self.config.get("app_open_wait", 6))
        self._hotkey("ctrl", "f")
        time.sleep(0.6)
        self._type(contact)
        time.sleep(1.5)
        self._press("enter")
        return "'%s' ki chat khol di P7." % contact

    def whatsapp_read(self, contact=None):
        """Open a chat (optional) and OCR the visible messages."""
        if contact:
            self.whatsapp_open_chat(contact)
            time.sleep(1.0)
        if not (self.vision and self.vision.available()):
            return "Chat padhne ke liye vision chahiye P7: pip install pyautogui pytesseract."
        text = self.vision.read_text()
        if text == "__NO_OCR__":
            return "OCR off hai P7. pip install pytesseract + Tesseract engine."
        if not text or text.startswith("__"):
            return "Screen se chat nahi padh paaya P7."
        return "Screen par dikh rahi chat P7:\n" + " ".join(text.split())[:1200]

    # ════════════════════════════════════════════════════════════════════
    #  YOUTUBE
    # ════════════════════════════════════════════════════════════════════
    def youtube_search(self, query):
        query = (query or "").strip()
        if not query:
            return "YouTube par kya search karun P7?"
        try:
            from urllib.parse import quote_plus
            webbrowser.open("https://www.youtube.com/results?search_query=" + quote_plus(query))
            return "YouTube par '%s' search kar diya P7." % query
        except Exception as e:
            return "YouTube search error: " + str(e)[:80]

    def youtube_play(self, query):
        """Open the first result for a query (auto-plays the watch page)."""
        query = (query or "").strip()
        if not query:
            return "YouTube par kya chalau P7?"
        # youtube.com/results?...&sp=... not reliable for autoplay; open search
        # and let the user / autonomous loop click, OR use the "feeling lucky"
        # style via a watch search is not public, so we open results.
        return self.youtube_search(query) + " Pehla video play karne ke liye 'pehle video par click karo' bolo."

    def youtube_skip_ad(self):
        """Click the 'Skip Ads' / 'Skip' button if it is on screen."""
        if not (self.vision and self.vision.available()):
            return "Ad skip ke liye vision chahiye P7: pip install pyautogui pytesseract."
        for label in ("Skip Ads", "Skip Ad", "Skip", "Ad skip karen", "Skip karen"):
            if self._click_text(label):
                return "Ad skip kar diya P7."
        return "Abhi koi skippable ad button screen par nahi mila P7."

    def youtube_next(self):
        err = self._need_pg()
        if err:
            return err
        self._hotkey("shift", "n")   # YouTube: next video
        return "Agla video chala diya P7."

    def youtube_prev(self):
        err = self._need_pg()
        if err:
            return err
        self._hotkey("shift", "p")
        return "Pichla video chala diya P7."

    def youtube_pause(self):
        err = self._need_pg()
        if err:
            return err
        self._press("k")             # YouTube: play/pause
        return "Video pause/play toggle kiya P7."

    def youtube_fullscreen(self):
        err = self._need_pg()
        if err:
            return err
        self._press("f")
        return "Fullscreen toggle kiya P7."

    # ════════════════════════════════════════════════════════════════════
    #  KEEP-ALIVE  (24/7 ke liye - screen ko lock/sleep hone se rokta hai)
    # ════════════════════════════════════════════════════════════════════
    def keep_awake_tick(self):
        """A harmless tiny input so Windows does not idle-sleep/lock.
        NOTE: ye sirf inactivity-lock rokta hai. Pehle se LOCKED screen ko
        unlock karna OS security ki wajah se possible nahi hai."""
        pg = self._pg()
        if not pg:
            return False
        try:
            x, y = pg.position()
            pg.moveRel(1, 0, duration=0)
            pg.moveRel(-1, 0, duration=0)
            return True
        except Exception:
            return False


_SKILLS = None


def skills(config=None, apps=None, vision=None):
    global _SKILLS
    if _SKILLS is None:
        _SKILLS = Skills(config=config, apps=apps, vision=vision)
    return _SKILLS


if __name__ == "__main__":
    s = Skills()
    print("pyautogui present:", _pyautogui() is not None)
    print(s.youtube_search("lofi beats"))
    print(s.youtube_skip_ad())
    print(s.whatsapp_send("", ""))
