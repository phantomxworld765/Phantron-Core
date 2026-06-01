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
                 vision=None, memory=None):
        self.cfg = cfg
        self.on_event = on_event
        self.vision = vision
        self.memory = memory
        self.guard = SafetyGuard(
            enabled=bool(cfg.get("agent.safe_mode", True)),
            allow_shutdown=bool(cfg.get("agent.allow_shutdown", False)),
        )
        self.name = cfg.get("user_name", "P7")

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
                        "question", "keys", "value", "key"):
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
]
