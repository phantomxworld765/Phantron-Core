# -*- coding: utf-8 -*-
"""
PHANTRON skills - the concrete things the assistant can DO.

Every skill is a small method that returns a short human-readable string.
Heavy/optional libraries (pyautogui, psutil) are imported lazily so the module
is import-safe on any OS and never crashes when a library is missing - the
skill just reports what to install instead.

The TOOLS list at the bottom is what the brain sees: a compact catalogue it
can choose from when acting autonomously.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

IS_WINDOWS = platform.system() == "Windows"

# Friendly name -> launch target (Windows). Unknown names fall back to the OS.
APPS = {
    "chrome": "chrome.exe", "brave": "brave.exe", "firefox": "firefox.exe",
    "edge": "msedge.exe", "notepad": "notepad.exe", "notepad++": "notepad++.exe",
    "vscode": "code.exe", "vs code": "code.exe", "code": "code.exe",
    "spotify": "spotify.exe", "discord": "discord.exe", "steam": "steam.exe",
    "vlc": "vlc.exe", "explorer": "explorer.exe", "files": "explorer.exe",
    "calculator": "calc.exe", "calc": "calc.exe", "paint": "mspaint.exe",
    "cmd": "cmd.exe", "terminal": "wt.exe", "powershell": "powershell.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:",
    "word": "WINWORD.EXE", "excel": "EXCEL.EXE", "powerpoint": "POWERPNT.EXE",
    "whatsapp": "whatsapp.exe", "telegram": "telegram.exe",
    "obs": "obs64.exe", "blender": "blender.exe",
}

QUICK_URLS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "chatgpt": "https://chat.openai.com",
    "maps": "https://maps.google.com",
    "stackoverflow": "https://stackoverflow.com",
}


class SafetyGuard:
    """Blocks irreversible, catastrophic shell commands."""

    DESTRUCTIVE = [
        r"\bformat\s+[a-z]:",
        r"\brd\s+/s\s+/q\s+[a-z]:\\?\s*$",
        r"\bdel\s+/[fsq]+\s+[a-z]:\\\*",
        r"remove-item\b.*\b[a-z]:\\\s*-recurse",
        r"\brm\s+-rf\s+/(?:\s|$)",
        r"\brm\s+-rf\s+~",
        r"\bmkfs\b", r"\bdd\s+if=.*of=/dev/",
        r"cipher\s+/w",
        r"diskpart\b.*\bclean\b",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",  # fork bomb
        r"format-volume",
    ]
    SHUTDOWN = [r"\bshutdown\b", r"restart-computer", r"stop-computer", r"\blogoff\b"]

    def __init__(self, enabled: bool = True, allow_shutdown: bool = False):
        self.enabled = enabled
        self.allow_shutdown = allow_shutdown

    def check(self, command: str):
        if not self.enabled:
            return True, ""
        low = (command or "").lower()
        for pat in self.DESTRUCTIVE:
            if re.search(pat, low):
                return False, "blocked dangerous command"
        if not self.allow_shutdown:
            for pat in self.SHUTDOWN:
                if re.search(pat, low):
                    return False, "shutdown/restart blocked (enable agent.allow_shutdown)"
        return True, ""


def _pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except Exception:
        return None


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


class Skills:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None,
                 vision=None, memory=None,
                 notify: Optional[Callable[[str], None]] = None,
                 security=None, messaging=None, selfheal=None):
        self.cfg = cfg
        self.on_event = on_event
        self.vision = vision
        self.memory = memory
        self.security = security
        self.messaging = messaging
        self.selfheal = selfheal
        # notify(text): used by reminders/timers to speak + surface to the UI
        self.notify = notify
        self.guard = SafetyGuard(
            enabled=bool(cfg.get("agent.safe_mode", True)),
            allow_shutdown=bool(cfg.get("agent.allow_shutdown", False)),
        )
        self.name = cfg.get("user_name", "P7")
        self._timers: list = []   # keep references so they aren't GC'd

    def _log(self, action: str, detail: str = "", kind: str = "tool"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  Launch apps / URLs
    # ─────────────────────────────────────────────────────────────────────
    def open_app(self, target: str = "") -> str:
        t = (target or "").strip()
        if not t:
            return "Kya kholun, %s?" % self.name
        self._log("Opening", t)
        tl = t.lower()

        if t.startswith(("http://", "https://", "www.")):
            url = t if t.startswith("http") else "https://" + t
            webbrowser.open(url)
            return t + " khol diya."
        if tl in QUICK_URLS:
            webbrowser.open(QUICK_URLS[tl])
            return tl + " khol diya."

        target_exe = APPS.get(tl, t)
        try:
            if IS_WINDOWS:
                if target_exe.startswith("ms-"):
                    subprocess.Popen(["cmd", "/c", "start", "", target_exe], shell=False)
                else:
                    os.startfile(target_exe)  # type: ignore[attr-defined]
            else:
                subprocess.Popen([target_exe])
            return t + " khol diya."
        except Exception:
            # last resort: ask the OS to resolve the name
            try:
                if IS_WINDOWS:
                    subprocess.Popen('start "" "%s"' % target_exe, shell=True)
                    return t + " khol diya."
            except Exception as exc:
                return "Nahi khul saka: " + str(exc)
            return "Nahi khul saka: " + t

    def play_song(self, query: str = "") -> str:
        q = (query or "trending songs").strip()
        self._log("Playing", q)
        try:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PHANTRON/2"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", "replace")
            match = re.search(r'"videoId":"([\w-]{11})"', html)
            if match:
                webbrowser.open("https://www.youtube.com/watch?v=" + match.group(1))
                return "'%s' chala diya." % q
        except Exception:
            pass
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q))
        return "'%s' YouTube pe khol diya." % q

    def web_search(self, query: str = "") -> str:
        q = (query or "").strip()
        if not q:
            return "Kya search karun?"
        self._log("Searching", q)
        # Try a quick instant-answer first (no key needed), else open the browser.
        try:
            api = "https://api.duckduckgo.com/?q=%s&format=json&no_html=1" % urllib.parse.quote_plus(q)
            req = urllib.request.Request(api, headers={"User-Agent": "PHANTRON/2"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            answer = data.get("AbstractText") or data.get("Answer")
            if answer:
                return str(answer)[:400]
        except Exception:
            pass
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
        return "'%s' browser me search kar diya." % q

    # ─────────────────────────────────────────────────────────────────────
    #  System / media
    # ─────────────────────────────────────────────────────────────────────
    def system_info(self, _: str = "") -> str:
        ps = _psutil()
        if not ps:
            return "System info ke liye psutil chahiye (pip install psutil)."
        try:
            mem = ps.virtual_memory()
            parts = [
                "CPU: %s%%" % ps.cpu_percent(interval=0.5),
                "RAM: %s%% (%.1f / %.1f GB)" % (
                    mem.percent, mem.used / 1073741824, mem.total / 1073741824),
            ]
            battery = ps.sensors_battery() if hasattr(ps, "sensors_battery") else None
            if battery:
                parts.append("Battery: %d%%%s" % (
                    int(battery.percent), " (charging)" if battery.power_plugged else ""))
            return " | ".join(parts)
        except Exception as exc:
            return "Error: " + str(exc)

    def media(self, action: str = "play_pause") -> str:
        keymap = {
            "play_pause": "playpause", "play": "playpause", "pause": "playpause",
            "next": "nexttrack", "previous": "prevtrack", "prev": "prevtrack",
            "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
        }
        key = keymap.get((action or "").lower())
        if not key:
            return "Unknown media action: " + str(action)
        self._log("Media", action)
        pag = _pyautogui()
        if pag:
            try:
                pag.press(key)
                return action + " done."
            except Exception:
                pass
        return "Media control ke liye pyautogui chahiye."

    def screenshot(self, _: str = "") -> str:
        pag = _pyautogui()
        if not pag:
            return "Screenshot ke liye pyautogui chahiye (pip install pyautogui Pillow)."
        try:
            path = self.cfg.files_dir() / ("screenshot_%d.png" % int(time.time()))
            pag.screenshot(str(path))
            self._log("Screenshot", str(path), "done")
            return "Screenshot save ki: " + str(path)
        except Exception as exc:
            return "Error: " + str(exc)

    def mouse(self, action: str = "click", x: Any = None, y: Any = None, amount: Any = 3) -> str:
        pag = _pyautogui()
        if not pag:
            return "Mouse control ke liye pyautogui chahiye."
        self._log("Mouse", action)
        try:
            w, h = pag.size()
            px = int(x) if x is not None else w // 2
            py = int(y) if y is not None else h // 2
            act = (action or "click").lower()
            if act == "click":
                pag.click(px, py); return "Click %d,%d" % (px, py)
            if act == "double_click":
                pag.doubleClick(px, py); return "Double click."
            if act == "right_click":
                pag.rightClick(px, py); return "Right click."
            if act == "move":
                pag.moveTo(px, py, duration=0.3); return "Moved %d,%d" % (px, py)
            if act == "scroll_up":
                pag.scroll(int(amount) * 120); return "Scrolled up."
            if act == "scroll_down":
                pag.scroll(-int(amount) * 120); return "Scrolled down."
            return "Unknown mouse action: " + act
        except Exception as exc:
            return "Error: " + str(exc)

    def type_text(self, text: str = "") -> str:
        pag = _pyautogui()
        if not pag:
            return "Typing ke liye pyautogui chahiye."
        self._log("Typing", str(text)[:40])
        try:
            time.sleep(0.2)
            pag.write(str(text), interval=0.02)
            return "Type kiya."
        except Exception as exc:
            return "Error: " + str(exc)

    def press_key(self, key: str = "enter") -> str:
        pag = _pyautogui()
        if not pag:
            return "Key press ke liye pyautogui chahiye."
        try:
            pag.press(str(key))
            return str(key) + " press ki."
        except Exception as exc:
            return "Error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Files
    # ─────────────────────────────────────────────────────────────────────
    def create_file(self, path: str = "", content: str = "") -> str:
        if not path:
            return "Filename do."
        try:
            p = Path(path)
            if not p.is_absolute():
                p = self.cfg.files_dir() / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content or "", encoding="utf-8")
            self._log("File created", str(p), "done")
            return "File ban gayi: " + str(p)
        except Exception as exc:
            return "Error: " + str(exc)

    def read_file(self, path: str = "") -> str:
        if not path:
            return "Filename do."
        try:
            p = Path(path)
            if not p.is_absolute():
                p = self.cfg.files_dir() / p
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:2000]
        except Exception as exc:
            return "Error: " + str(exc)

    def list_dir(self, path: str = "") -> str:
        try:
            p = Path(path) if path else self.cfg.files_dir()
            items = sorted(os.listdir(p))
            return "\n".join(items[:80]) if items else "(empty)"
        except Exception as exc:
            return "Error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Shell (guarded)
    # ─────────────────────────────────────────────────────────────────────
    def run_command(self, command: str = "", shell: str = "auto") -> str:
        cmd = (command or "").strip()
        if not cmd:
            return "Konsa command?"
        ok, reason = self.guard.check(cmd)
        if not ok:
            self._log("Blocked", cmd[:60], "error")
            return "Safety: " + reason
        self._log("Command", cmd[:60])
        try:
            if IS_WINDOWS:
                if shell == "cmd":
                    args = ["cmd", "/c", cmd]
                else:
                    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
            else:
                args = ["bash", "-lc", cmd]
            result = subprocess.run(
                args, capture_output=True, text=True,
                timeout=int(self.cfg.get("agent.command_timeout", 120)),
                encoding="utf-8", errors="replace",
            )
            out = ((result.stdout or "") + (result.stderr or "")).strip()
            return out[:2000] if out else "Done."
        except subprocess.TimeoutExpired:
            return "Command timeout."
        except Exception as exc:
            return "Error: " + str(exc)

    def tell_time(self, _: str = "") -> str:
        from datetime import datetime
        return datetime.now().strftime("Abhi %I:%M %p, %A %d %B %Y hai.")

    # ─────────────────────────────────────────────────────────────────────
    #  Vision (see + read the screen)
    # ─────────────────────────────────────────────────────────────────────
    def see_screen(self, question: str = "What is on the screen right now?") -> str:
        if not self.vision:
            return "Vision module load nahi hua."
        return self.vision.describe(question)

    def read_screen(self, _: str = "") -> str:
        if not self.vision:
            return "Vision module load nahi hua."
        return self.vision.read_screen()

    def click_text(self, text: str = "") -> str:
        """Find on-screen text via OCR and click it."""
        if not self.vision:
            return "Vision module load nahi hua."
        if not text:
            return "Kis text pe click karun?"
        spot = self.vision.find_text(text)
        if not spot:
            return "Screen pe '%s' nahi mila." % text
        return self.mouse("click", spot[0], spot[1])

    # ─────────────────────────────────────────────────────────────────────
    #  Memory
    # ─────────────────────────────────────────────────────────────────────
    def remember(self, key: str = "", value: str = "") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        return self.memory.remember(key, value)

    def recall(self, key: str = "") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        return self.memory.recall(key)

    def forget(self, key: str = "") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        return self.memory.forget(key)

    # ─────────────────────────────────────────────────────────────────────
    #  Clipboard, hotkeys, window control
    # ─────────────────────────────────────────────────────────────────────
    def clipboard(self, action: str = "get", text: str = "") -> str:
        try:
            import pyperclip
            if action == "set":
                pyperclip.copy(text or "")
                return "Clipboard set."
            return pyperclip.paste() or "(clipboard empty)"
        except Exception:
            # fallback to OS tools
            try:
                if IS_WINDOWS and action == "get":
                    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                                         capture_output=True, text=True, timeout=10)
                    return (out.stdout or "").strip() or "(clipboard empty)"
            except Exception:
                pass
            return "Clipboard ke liye pyperclip chahiye (pip install pyperclip)."

    def hotkey(self, keys: str = "") -> str:
        """Press a combo like 'ctrl+s', 'alt+tab', 'win+d'."""
        pag = _pyautogui()
        if not pag:
            return "Hotkey ke liye pyautogui chahiye."
        combo = [k.strip().lower() for k in re.split(r"[+\s]+", keys or "") if k.strip()]
        if not combo:
            return "Konsa combo?"
        try:
            pag.hotkey(*combo)
            return "+".join(combo) + " press kiya."
        except Exception as exc:
            return "Error: " + str(exc)

    def window(self, action: str = "minimize") -> str:
        """Window control via global hotkeys (Windows)."""
        pag = _pyautogui()
        if not pag:
            return "Window control ke liye pyautogui chahiye."
        act = (action or "").lower()
        try:
            if act in ("minimize", "min"):
                pag.hotkey("win", "down"); return "Window minimize."
            if act in ("maximize", "max"):
                pag.hotkey("win", "up"); return "Window maximize."
            if act in ("close",):
                pag.hotkey("alt", "f4"); return "Window band."
            if act in ("switch", "alt_tab"):
                pag.hotkey("alt", "tab"); return "Switched."
            if act in ("show_desktop", "desktop"):
                pag.hotkey("win", "d"); return "Desktop."
            if act in ("snap_left", "left"):
                pag.hotkey("win", "left"); return "Snapped left."
            if act in ("snap_right", "right"):
                pag.hotkey("win", "right"); return "Snapped right."
            return "Unknown window action: " + act
        except Exception as exc:
            return "Error: " + str(exc)

    def write_in(self, text: str = "", enter: Any = False) -> str:
        """Type text into the focused field, optionally pressing Enter."""
        res = self.type_text(text)
        if enter in (True, "true", "1", 1):
            self.press_key("enter")
        return res

    # ─────────────────────────────────────────────────────────────────────
    #  Reminders & timers (background threads, persisted via memory)
    # ─────────────────────────────────────────────────────────────────────
    def _fire(self, message: str, reminder_id: Any = None):
        msg = "Reminder, %s: %s" % (self.name, message)
        self._log("Reminder", message, "speak")
        if reminder_id is not None and self.memory:
            try:
                self.memory.mark_reminder_done(int(reminder_id))
            except Exception:
                pass
        if self.notify:
            try:
                self.notify(msg)
            except Exception:
                pass

    def _schedule_at(self, message: str, fire_at: float, reminder_id: Any = None):
        """Schedule a reminder for an absolute epoch time (used on restart too)."""
        import threading
        delay = max(0.0, float(fire_at) - time.time())
        t = threading.Timer(delay, self._fire, args=(message, reminder_id))
        t.daemon = True
        t.start()
        self._timers.append(t)

    def reschedule_pending(self) -> int:
        """Re-arm reminders saved from previous sessions. Returns how many."""
        if not self.memory:
            return 0
        count = 0
        for r in self.memory.pending_reminders():
            self._schedule_at(r.get("text", "reminder"), r.get("fire_at", time.time()), r.get("id"))
            count += 1
        if count:
            self._log("Reminders restored", "%d pending" % count)
        return count

    def set_reminder(self, text: str = "", minutes: Any = 0, seconds: Any = 0) -> str:
        """Remind P7 after a delay. e.g. set_reminder('chai', minutes=10)."""
        try:
            delay = int(float(minutes or 0)) * 60 + int(float(seconds or 0))
        except Exception:
            delay = 0
        if delay <= 0:
            return "Kitni der baad yaad dilaun? (minutes/seconds do)"
        fire_at = time.time() + delay
        rid = None
        if self.memory:
            try:
                rid = self.memory.add_reminder(text or "reminder", fire_at).get("id")
            except Exception:
                rid = None
        self._schedule_at(text or "reminder", fire_at, rid)
        when = "%d min" % (delay // 60) if delay >= 60 else "%d sec" % delay
        self._log("Reminder set", "%s in %s" % (text, when))
        return "Theek hai %s, %s baad yaad dila dunga: %s" % (self.name, when, text)

    def timer(self, seconds: Any = 0, minutes: Any = 0) -> str:
        """A simple countdown timer that announces when done."""
        return self.set_reminder("Timer pura hua!", minutes=minutes, seconds=seconds)

    def list_reminders(self, _: str = "") -> str:
        if not self.memory:
            return "Reminder memory load nahi hui."
        return self.memory.list_reminders()

    # ─────────────────────────────────────────────────────────────────────
    #  Notes & to-do (persisted as plain text files)
    # ─────────────────────────────────────────────────────────────────────
    def _notes_file(self, name: str = "notes") -> Path:
        return self.cfg.files_dir() / ("%s.txt" % re.sub(r"[^\w.-]", "_", name or "notes"))

    def add_note(self, text: str = "", name: str = "notes") -> str:
        if not text:
            return "Kya note karun?"
        from datetime import datetime
        line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M"), text)
        try:
            p = self._notes_file(name)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            self._log("Note added", text[:40], "done")
            return "Note save kar liya, %s." % self.name
        except Exception as exc:
            return "Error: " + str(exc)

    def read_notes(self, name: str = "notes") -> str:
        try:
            p = self._notes_file(name)
            if not p.exists():
                return "Abhi koi note nahi hai."
            return p.read_text(encoding="utf-8", errors="replace")[:2000] or "(empty)"
        except Exception as exc:
            return "Error: " + str(exc)

    def add_todo(self, text: str = "") -> str:
        return self.add_note("[ ] " + text, name="todo") if text else "Kya add karun?"

    def list_todo(self, _: str = "") -> str:
        return self.read_notes("todo")

    # ─────────────────────────────────────────────────────────────────────
    #  Weather & news (free, no API key)
    # ─────────────────────────────────────────────────────────────────────
    def weather(self, city: str = "") -> str:
        c = (city or "").strip()
        try:
            url = "https://wttr.in/%s?format=%%l:+%%c+%%t+(feels+%%f),+%%h+humidity,+wind+%%w" % urllib.parse.quote(c)
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                out = resp.read().decode("utf-8", "replace").strip()
            self._log("Weather", c or "auto")
            return out[:300] if out else "Weather nahi mila."
        except Exception:
            return "Weather laane me dikkat - internet check kar lo."

    def news(self, topic: str = "") -> str:
        """Top headlines via Google News RSS (no key)."""
        try:
            if topic:
                url = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(topic)
            else:
                url = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
            req = urllib.request.Request(url, headers={"User-Agent": "PHANTRON/2"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml = resp.read().decode("utf-8", "replace")
            titles = re.findall(r"<title>(.*?)</title>", xml)
            titles = [re.sub(r"<!\[CDATA\[|\]\]>", "", t).strip() for t in titles[1:6]]
            self._log("News", topic or "top")
            if titles:
                return "Top khabrein:\n- " + "\n- ".join(titles)
            return "Koi news nahi mili."
        except Exception:
            return "News laane me dikkat - internet check kar lo."

    # ─────────────────────────────────────────────────────────────────────
    #  Calculator (safe arithmetic eval)
    # ─────────────────────────────────────────────────────────────────────
    def calc(self, expression: str = "") -> str:
        expr = (expression or "").strip()
        if not expr:
            return "Kya calculate karun?"
        if not re.fullmatch(r"[\d\s+\-*/().%^]+", expr):
            return "Sirf numbers aur + - * / ( ) % ^ allowed hain."
        try:
            safe = expr.replace("^", "**")
            result = eval(safe, {"__builtins__": {}}, {})  # noqa: S307 - input is whitelisted above
            return "%s = %s" % (expr, result)
        except Exception:
            return "Ye calculate nahi kar paya."

    # ─────────────────────────────────────────────────────────────────────
    #  Process & system control
    # ─────────────────────────────────────────────────────────────────────
    def close_app(self, name: str = "") -> str:
        n = (name or "").strip()
        if not n:
            return "Kis app ko band karun?"
        exe = APPS.get(n.lower(), n)
        if not exe.lower().endswith(".exe"):
            exe += ".exe"
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, timeout=15)
            else:
                subprocess.run(["pkill", "-f", n], capture_output=True, timeout=15)
            self._log("Closed", n, "done")
            return "%s band kar diya." % n
        except Exception as exc:
            return "Error: " + str(exc)

    def list_processes(self, _: str = "") -> str:
        ps = _psutil()
        if not ps:
            return "Process list ke liye psutil chahiye."
        try:
            procs = []
            for p in ps.process_iter(["name", "memory_percent"]):
                try:
                    procs.append((p.info["name"], p.info["memory_percent"] or 0))
                except Exception:
                    continue
            procs.sort(key=lambda x: x[1], reverse=True)
            top = ["%s (%.1f%%)" % (n, m) for n, m in procs[:8] if n]
            return "Top processes (RAM):\n- " + "\n- ".join(top)
        except Exception as exc:
            return "Error: " + str(exc)

    def lock_pc(self, _: str = "") -> str:
        try:
            if IS_WINDOWS:
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], timeout=10)
                return "PC lock kar diya."
            return "Lock sirf Windows pe supported hai abhi."
        except Exception as exc:
            return "Error: " + str(exc)

    def power(self, action: str = "", minutes: Any = 0) -> str:
        """shutdown / restart / sleep / cancel - gated by agent.allow_shutdown."""
        act = (action or "").lower()
        if not self.cfg.get("agent.allow_shutdown", False):
            return "Power control band hai. config me agent.allow_shutdown=true karo."
        try:
            mins = int(float(minutes or 0))
            if not IS_WINDOWS:
                return "Power control abhi sirf Windows pe."
            if act in ("shutdown", "off"):
                subprocess.run(["shutdown", "/s", "/t", str(mins * 60)], timeout=10)
                return "Shutdown %s min me." % mins
            if act in ("restart", "reboot"):
                subprocess.run(["shutdown", "/r", "/t", str(mins * 60)], timeout=10)
                return "Restart %s min me." % mins
            if act in ("sleep",):
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=10)
                return "Sleep mode."
            if act in ("cancel", "abort"):
                subprocess.run(["shutdown", "/a"], timeout=10)
                return "Shutdown cancel kar diya."
            return "Unknown power action."
        except Exception as exc:
            return "Error: " + str(exc)

    def volume(self, level: Any = None) -> str:
        """Set absolute volume 0-100 (needs pyautogui for fallback steps)."""
        pag = _pyautogui()
        if level is None:
            return "Kितना volume? (0-100)"
        try:
            lvl = max(0, min(100, int(float(level))))
        except Exception:
            return "Volume 0-100 me do."
        if pag:
            # rough: mute then step up to ~level in 2% increments
            try:
                for _ in range(50):
                    pag.press("volumedown")
                for _ in range(lvl // 2):
                    pag.press("volumeup")
                return "Volume ~%d%% set kiya." % lvl
            except Exception:
                pass
        return "Volume set karne ke liye pyautogui chahiye."

    def open_folder(self, name: str = "") -> str:
        """Open common Windows folders by name (downloads, documents, etc.)."""
        n = (name or "").lower().strip()
        known = {
            "downloads": Path.home() / "Downloads",
            "documents": Path.home() / "Documents",
            "desktop": Path.home() / "Desktop",
            "pictures": Path.home() / "Pictures",
            "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
            "home": Path.home(),
            "phantron": self.cfg.files_dir(),
        }
        target = known.get(n, n)
        try:
            if IS_WINDOWS:
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return "%s folder khol diya." % n
        except Exception as exc:
            return "Error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Vision + self-heal combo: read an on-screen error and explain/fix it
    # ─────────────────────────────────────────────────────────────────────
    def fix_screen_error(self, _: str = "") -> str:
        """Look at the screen, find an error message, and explain how to fix it."""
        if not self.vision:
            return "Vision module load nahi hua."
        err = self.vision.read_error()
        if err.startswith("__UNAVAILABLE__:"):
            return err.split(":", 1)[1]
        if not err:
            return "Screen pe koi obvious error message nahi dikha, %s." % self.name
        self._log("Screen error", err[:60], "vision")
        # Ask the brain (via selfheal) to explain + suggest a fix, if online.
        if self.selfheal and getattr(self.selfheal, "brain", None) and self.selfheal.brain.online:
            from .brain import ERR as _ERR
            out = self.selfheal.brain.ask(
                "You are PHANTRON. The user pointed you at their screen and this error "
                "text was read via OCR. In short Hinglish, explain what it means and the "
                "most likely fix (2-4 lines).",
                err,
            )
            if not out.startswith(_ERR):
                return "Screen pe ye error dikha:\n%s\n\n-> %s" % (err[:200], out.strip())
        return ("Screen pe ye error/warning dikha:\n%s\n\n(Brain online karo to main "
                "iska fix bhi bata dunga, %s.)" % (err[:400], self.name))

    # ─────────────────────────────────────────────────────────────────────
    #  Scheduled (daily/recurring) reminders
    # ─────────────────────────────────────────────────────────────────────
    def schedule_reminder(self, text: str = "", time_str: str = "", repeat: str = "daily") -> str:
        """Set a recurring reminder, e.g. schedule_reminder('gym', '18:30')."""
        if not self.memory:
            return "Schedule ke liye memory module chahiye."
        if not text:
            return "Kya yaad dilana hai?"
        hh, mm = self._parse_time(time_str)
        if hh is None:
            return "Time samajh nahi aaya. Aise do: '9:00', '18:30', '7 pm'."
        self.memory.add_schedule(text, hh, mm, repeat or "daily")
        self._ensure_schedule_checker()
        self._log("Schedule set", "%s @ %02d:%02d" % (text, hh, mm))
        return "Set kar diya %s: roz %02d:%02d baje yaad dilaunga -> %s" % (self.name, hh, mm, text)

    def list_schedules(self, _: str = "") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        return self.memory.list_schedules()

    def remove_schedule(self, index: Any = 0) -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        try:
            i = int(index) - 1
        except Exception:
            return "Konsa number? 'list schedules' se dekho."
        return "Schedule hata diya." if self.memory.remove_schedule(i) else "Wo schedule mila nahi."

    def _parse_time(self, s: str):
        """Parse '9:00', '18:30', '7pm', '7 am' -> (hour24, minute) or (None,None)."""
        s = (s or "").strip().lower()
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
        if not m:
            return None, None
        hh = int(m.group(1)); mm = int(m.group(2) or 0); ap = m.group(3)
        if ap == "pm" and hh < 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
        return None, None

    def _ensure_schedule_checker(self):
        """Start a single background loop that fires due daily reminders."""
        if getattr(self, "_sched_running", False):
            return
        self._sched_running = True
        import threading
        from datetime import datetime

        def loop():
            while True:
                try:
                    now = datetime.now()
                    day = now.strftime("%Y-%m-%d")
                    for s in (self.memory.all_schedules() if self.memory else []):
                        if s.get("hour") == now.hour and s.get("minute") == now.minute \
                                and s.get("last_fired") != day:
                            self.memory.mark_schedule_fired(s["id"], day)
                            self._fire(s.get("text", "reminder"))
                except Exception:
                    pass
                time.sleep(30)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._timers.append(t)

    def start_schedules(self) -> int:
        """Called at startup: launch the checker if any schedules exist."""
        if not self.memory:
            return 0
        scheds = self.memory.all_schedules()
        if scheds:
            self._ensure_schedule_checker()
        return len(scheds)

    # ─────────────────────────────────────────────────────────────────────
    #  File search ("mera resume kahan hai")
    # ─────────────────────────────────────────────────────────────────────
    def find_files(self, query: str = "", where: str = "") -> str:
        q = (query or "").strip().lower()
        if not q:
            return "Kya dhundu? (file ka naam ya part)"
        roots = []
        if where:
            roots = [Path(where)]
        else:
            home = Path.home()
            for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
                p = home / sub
                if p.exists():
                    roots.append(p)
            if not roots:
                roots = [home]
        self._log("Searching files", q)
        matches: List[str] = []
        scanned = 0
        try:
            for root in roots:
                for dirpath, dirnames, filenames in os.walk(root):
                    # skip hidden/system dirs to stay fast
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fn in filenames:
                        scanned += 1
                        if q in fn.lower():
                            matches.append(str(Path(dirpath) / fn))
                            if len(matches) >= 25:
                                break
                        if scanned > 60000:
                            break
                    if len(matches) >= 25 or scanned > 60000:
                        break
                if len(matches) >= 25:
                    break
        except Exception as exc:
            return "Search error: " + str(exc)
        if not matches:
            return "'%s' naam ki koi file nahi mili, %s." % (query, self.name)
        return "Mili (%d):\n- %s" % (len(matches), "\n- ".join(matches[:25]))

    # ─────────────────────────────────────────────────────────────────────
    #  Auto-backup a folder (zip into the Phantron backups dir)
    # ─────────────────────────────────────────────────────────────────────
    def backup_folder(self, source: str = "", name: str = "") -> str:
        src = (source or "").strip()
        if not src:
            return "Kis folder ka backup lun? (path do)"
        srcp = Path(src)
        if not srcp.exists():
            # try a known folder name
            known = {"documents": Path.home() / "Documents",
                     "desktop": Path.home() / "Desktop",
                     "pictures": Path.home() / "Pictures",
                     "downloads": Path.home() / "Downloads"}
            srcp = known.get(src.lower(), srcp)
        if not srcp.exists():
            return "Folder nahi mila: " + src
        try:
            import shutil
            backups = self.cfg.files_dir().parent / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = backups / ("%s_%s" % (name or srcp.name, stamp))
            self._log("Backing up", str(srcp))
            archive = shutil.make_archive(str(base), "zip", str(srcp))
            size_mb = Path(archive).stat().st_size / 1048576
            return "Backup ban gaya (%.1f MB): %s" % (size_mb, archive)
        except Exception as exc:
            return "Backup error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Voice authentication (passphrase gate - simple, local)
    # ─────────────────────────────────────────────────────────────────────
    def set_voice_passphrase(self, phrase: str = "") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        p = (phrase or "").strip().lower()
        if not p:
            return "Passphrase bolo/likho jo set karni hai."
        self.memory.remember("__voice_passphrase__", p)
        return "Voice passphrase set kar di, %s. Ab sensitive commands se pehle ye poochi jayegi." % self.name

    def check_passphrase(self, phrase: str = "") -> bool:
        if not self.memory:
            return True
        saved = self.memory.recall("__voice_passphrase__")
        if not saved or "kuch yaad nahi" in saved:
            return True  # no passphrase set -> open
        return (phrase or "").strip().lower() == saved.strip().lower()

    # ─────────────────────────────────────────────────────────────────────
    #  Security / defensive system (delegates to Security module)
    # ─────────────────────────────────────────────────────────────────────
    def security_audit(self, _: str = "") -> str:
        return self.security.audit() if self.security else "Security module load nahi hua."

    def antivirus_scan(self, mode: str = "quick") -> str:
        return self.security.antivirus_scan(mode) if self.security else "Security module load nahi hua."

    def defender_status(self, _: str = "") -> str:
        return self.security.defender_status() if self.security else "Security module load nahi hua."

    def firewall(self, action: str = "status") -> str:
        return self.security.firewall(action) if self.security else "Security module load nahi hua."

    def open_ports(self, _: str = "") -> str:
        return self.security.open_ports() if self.security else "Security module load nahi hua."

    def network_connections(self, _: str = "") -> str:
        return self.security.connections() if self.security else "Security module load nahi hua."

    def scan_suspicious(self, _: str = "") -> str:
        return self.security.suspicious() if self.security else "Security module load nahi hua."

    def quarantine_file(self, path: str = "") -> str:
        return self.security.quarantine(path) if self.security else "Security module load nahi hua."

    def harden_tips(self, _: str = "") -> str:
        return self.security.harden() if self.security else "Security module load nahi hua."

    # ─────────────────────────────────────────────────────────────────────
    #  Messaging & email (delegates to Messaging module)
    # ─────────────────────────────────────────────────────────────────────
    def send_whatsapp(self, to: str = "", message: str = "") -> str:
        return self.messaging.whatsapp(to, message) if self.messaging else "Messaging module load nahi hua."

    def send_telegram(self, message: str = "", chat_id: str = "") -> str:
        return self.messaging.telegram(message, chat_id) if self.messaging else "Messaging module load nahi hua."

    def send_email(self, to: str = "", subject: str = "", body: str = "") -> str:
        return self.messaging.send_email(to, subject, body) if self.messaging else "Messaging module load nahi hua."

    def read_email(self, count: Any = 5) -> str:
        try:
            n = int(count)
        except Exception:
            n = 5
        return self.messaging.read_email(n) if self.messaging else "Messaging module load nahi hua."

    # ─────────────────────────────────────────────────────────────────────
    #  Self-healing (delegates to SelfHeal module)
    # ─────────────────────────────────────────────────────────────────────
    def heal_code(self, path: str = "", error: str = "") -> str:
        return self.selfheal.heal_file(path, error) if self.selfheal else "Self-heal module load nahi hua."

    def explain_error(self, _: str = "") -> str:
        return self.selfheal.explain_last() if self.selfheal else "Self-heal module load nahi hua."

    # ─────────────────────────────────────────────────────────────────────
    #  Conversation export (delegates to Memory module)
    # ─────────────────────────────────────────────────────────────────────
    def export_chat(self, fmt: str = "txt") -> str:
        if not self.memory:
            return "Memory module load nahi hua."
        try:
            out = self.memory.export_conversation((fmt or "txt").lower())
            self._log("Exported chat", str(out), "done")
            return "Conversation export kar di: " + str(out)
        except Exception as exc:
            return "Export error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Dispatch
    # ─────────────────────────────────────────────────────────────────────
    def execute(self, tool: str, args: Dict[str, Any]) -> str:
        fn = getattr(self, tool, None)
        if not callable(fn):
            return "Unknown tool: " + str(tool)
        try:
            return fn(**(args or {}))
        except TypeError:
            # tolerate a single positional-style arg under common keys
            for key in ("target", "query", "text", "command", "action", "path",
                        "question", "keys", "value", "key", "city", "topic",
                        "expression", "name", "level", "mode", "to", "message",
                        "subject", "body", "count", "fmt", "error", "chat_id",
                        "where", "source", "time_str", "phrase", "index"):
                if key in (args or {}):
                    return fn(args[key])
            return fn()
        except Exception as exc:
            return "Tool error: " + str(exc)


# Catalogue presented to the brain when it acts autonomously.
TOOLS: List[Dict[str, str]] = [
    {"name": "open_app", "args": "target", "desc": "Open an app, website name, or URL (e.g. chrome, youtube, notepad)"},
    {"name": "play_song", "args": "query", "desc": "Play a song/video on YouTube"},
    {"name": "web_search", "args": "query", "desc": "Search the web and return a quick answer"},
    {"name": "system_info", "args": "", "desc": "Report CPU, RAM and battery status"},
    {"name": "media", "args": "action", "desc": "Media keys: play_pause, next, previous, volume_up, volume_down, mute"},
    {"name": "screenshot", "args": "", "desc": "Capture the screen to a file"},
    {"name": "mouse", "args": "action,x,y,amount", "desc": "Mouse: click, double_click, right_click, move, scroll_up, scroll_down"},
    {"name": "type_text", "args": "text", "desc": "Type text on the keyboard"},
    {"name": "press_key", "args": "key", "desc": "Press a single key like enter, esc, tab"},
    {"name": "create_file", "args": "path,content", "desc": "Create/write a file"},
    {"name": "read_file", "args": "path", "desc": "Read a text file"},
    {"name": "list_dir", "args": "path", "desc": "List files in a folder"},
    {"name": "run_command", "args": "command,shell", "desc": "Run a shell command (PowerShell on Windows)"},
    {"name": "tell_time", "args": "", "desc": "Tell the current date and time"},
    {"name": "see_screen", "args": "question", "desc": "Look at the screen and describe/answer about it (vision)"},
    {"name": "read_screen", "args": "", "desc": "OCR: read all text currently on the screen"},
    {"name": "click_text", "args": "text", "desc": "Find on-screen text and click it"},
    {"name": "remember", "args": "key,value", "desc": "Permanently remember a fact about the user"},
    {"name": "recall", "args": "key", "desc": "Recall a remembered fact (empty key = list all)"},
    {"name": "forget", "args": "key", "desc": "Forget a remembered fact"},
    {"name": "clipboard", "args": "action,text", "desc": "Clipboard get/set (action: get|set)"},
    {"name": "hotkey", "args": "keys", "desc": "Press a key combo like 'ctrl+s', 'alt+tab', 'win+d'"},
    {"name": "window", "args": "action", "desc": "Window: minimize, maximize, close, switch, snap_left, snap_right, show_desktop"},
    {"name": "write_in", "args": "text,enter", "desc": "Type text into the focused field; enter=true to submit"},
    {"name": "set_reminder", "args": "text,minutes,seconds", "desc": "Remind the user after a delay"},
    {"name": "timer", "args": "minutes,seconds", "desc": "Start a countdown timer that announces when done"},
    {"name": "add_note", "args": "text", "desc": "Save a quick note"},
    {"name": "read_notes", "args": "", "desc": "Read saved notes"},
    {"name": "add_todo", "args": "text", "desc": "Add a to-do item"},
    {"name": "list_todo", "args": "", "desc": "List to-do items"},
    {"name": "weather", "args": "city", "desc": "Current weather (city optional = auto-locate)"},
    {"name": "news", "args": "topic", "desc": "Top news headlines (topic optional)"},
    {"name": "calc", "args": "expression", "desc": "Calculate an arithmetic expression"},
    {"name": "close_app", "args": "name", "desc": "Close/kill a running app by name"},
    {"name": "list_processes", "args": "", "desc": "List the top processes by memory"},
    {"name": "lock_pc", "args": "", "desc": "Lock the workstation"},
    {"name": "power", "args": "action,minutes", "desc": "shutdown/restart/sleep/cancel (gated by config)"},
    {"name": "volume", "args": "level", "desc": "Set system volume 0-100"},
    {"name": "open_folder", "args": "name", "desc": "Open a folder: downloads, documents, desktop, pictures, music, videos, home"},
    {"name": "list_reminders", "args": "", "desc": "List pending (saved) reminders"},
    {"name": "export_chat", "args": "fmt", "desc": "Export the conversation to a file (fmt: txt|json)"},
    # --- Security / defensive ---
    {"name": "security_audit", "args": "", "desc": "Full security health report with a score"},
    {"name": "antivirus_scan", "args": "mode", "desc": "Run Microsoft Defender scan (mode: quick|full)"},
    {"name": "defender_status", "args": "", "desc": "Antivirus real-time protection + definitions status"},
    {"name": "firewall", "args": "action", "desc": "Firewall: status | on | off"},
    {"name": "open_ports", "args": "", "desc": "List listening network ports + owning process"},
    {"name": "network_connections", "args": "", "desc": "List active external network connections"},
    {"name": "scan_suspicious", "args": "", "desc": "Heuristic scan for suspicious processes/commands"},
    {"name": "quarantine_file", "args": "path", "desc": "Move a flagged file into locked quarantine"},
    {"name": "harden_tips", "args": "", "desc": "Security hardening checklist"},
    # --- Messaging & email ---
    {"name": "send_whatsapp", "args": "to,message", "desc": "Send a WhatsApp message (to = number with country code)"},
    {"name": "send_telegram", "args": "message,chat_id", "desc": "Send a Telegram message via bot"},
    {"name": "send_email", "args": "to,subject,body", "desc": "Send an email"},
    {"name": "read_email", "args": "count", "desc": "Read the latest emails (headers)"},
    # --- Self-healing ---
    {"name": "heal_code", "args": "path,error", "desc": "Auto-fix a Python file (verified, with backup)"},
    {"name": "explain_error", "args": "", "desc": "Explain the most recent internal error"},
    {"name": "fix_screen_error", "args": "", "desc": "Look at the screen, read an error, and explain/fix it"},
    # --- Scheduling / files / backup / voice-auth ---
    {"name": "schedule_reminder", "args": "text,time_str,repeat", "desc": "Daily/recurring reminder, e.g. text='gym' time_str='18:30'"},
    {"name": "list_schedules", "args": "", "desc": "List daily/scheduled reminders"},
    {"name": "remove_schedule", "args": "index", "desc": "Remove a scheduled reminder by its list number"},
    {"name": "find_files", "args": "query,where", "desc": "Search for files by name (e.g. 'resume')"},
    {"name": "backup_folder", "args": "source,name", "desc": "Zip-backup a folder into the Phantron backups dir"},
    {"name": "set_voice_passphrase", "args": "phrase", "desc": "Set a passphrase to gate sensitive voice commands"},
]
