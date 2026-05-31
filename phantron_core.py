# -*- coding: utf-8 -*-
"""
PHANTRON v7  -  P7's autonomous, Jarvis-style AI assistant.

Runs ONE server that accepts commands from THREE sources at the same time:
  1. The web Interface.html  (text + mic button)
  2. The terminal that launched it  (just type and press Enter)
  3. Voice wake-word ("phantron ...")  if a microphone is available

Flow for every command:
  1. Fast LOCAL parser handles common things instantly (no AI, no cost).
  2. If not handled, the AI BRAIN (Ollama local, or Claude if a key is set)
     decides what to do and returns JSON tool-calls that get executed.
  3. PHANTRON speaks the reply and shows everything live on the interface.

The code never crashes if an optional library is missing - it degrades
gracefully (e.g. no microphone -> text still works; no TTS -> prints reply).
"""

import asyncio
import base64
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from io import BytesIO
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

try:
    import websockets
except ImportError:
    print("[FATAL] 'websockets' missing. Run: pip install -r requirements.txt")
    sys.exit(1)

IS_WINDOWS = platform.system() == "Windows"

# ════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════════════════
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT = {
    "ai_mode": "ollama",
    "claude_api_key": "",
    "claude_model": "claude-haiku-4-5-20251001",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434/api/generate",
    "ws_port": 8765,
    "user_name": "P7",
    "assistant_name": "PHANTRON",
    "wake_word": "phantron",
    "voice_output_enabled": True,
    "voice_input_enabled": True,
    "tts_engine": "auto",
    "tts_voice_edge": "hi-IN-MadhurNeural",
    "stt_language": "hi-IN",
    "autonomous": True,
    "max_agent_steps": 6,
    "open_interface": True,
    "screen_broadcast": True,
    "screen_interval_sec": 4,
    "metrics_interval_sec": 2,
    "projects_dir": str(Path.home() / "Phantron" / "Projects"),
    "files_dir": str(Path.home() / "Phantron" / "Files"),
    "agent_max_steps": 12,
    "agent_max_fix_attempts": 3,
    "agent_command_timeout": 900,
    "agent_safe_mode": True,
    "agent_allow_shutdown": False,
    "unreal_engine_dir": "",
    "android_sdk_dir": "",
}


def load_config():
    cfg = DEFAULT.copy()
    if CONFIG_FILE.exists():
        try:
            user = json.load(open(CONFIG_FILE, encoding="utf-8"))
            cfg.update({k: v for k, v in user.items()})
        except Exception as e:
            print("[CONFIG] Could not read config.json, using defaults:", e)
    for k, v in DEFAULT.items():
        cfg.setdefault(k, v)
    try:
        json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception:
        pass
    return cfg


CONFIG = load_config()


def projects_dir():
    p = Path(CONFIG.get("projects_dir") or (Path.home() / "Phantron" / "Projects"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def files_dir():
    p = Path(CONFIG.get("files_dir") or (Path.home() / "Phantron" / "Files"))
    p.mkdir(parents=True, exist_ok=True)
    return p


# ════════════════════════════════════════════════════════════════════════════
#  APP LAUNCH TABLE
# ════════════════════════════════════════════════════════════════════════════
BRAVE_PATHS = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
]

APPS = {
    "brave": BRAVE_PATHS[0], "chrome": "chrome.exe", "firefox": "firefox.exe",
    "edge": "msedge.exe", "notepad": "notepad.exe", "notepad++": "notepad++.exe",
    "vscode": "code.exe", "vs code": "code.exe", "code": "code.exe",
    "steam": "steam.exe", "discord": "Discord.exe", "spotify": "Spotify.exe",
    "vlc": "vlc.exe", "explorer": "explorer.exe", "calculator": "calc.exe",
    "calc": "calc.exe", "paint": "mspaint.exe", "task manager": "taskmgr.exe",
    "cmd": "cmd.exe", "terminal": "wt.exe", "powershell": "powershell.exe",
    "android studio": "studio64.exe", "maya": "maya.exe",
    "blender": "blender.exe", "unreal": "UnrealEditor.exe",
    "unreal engine": "UnrealEditor.exe", "obs": "obs64.exe",
    "pycharm": "pycharm64.exe", "photoshop": "Photoshop.exe",
    "premiere": "Adobe Premiere Pro.exe", "after effects": "AfterFX.exe",
    "illustrator": "Illustrator.exe", "whatsapp": "WhatsApp.exe",
    "telegram": "Telegram.exe", "word": "WINWORD.EXE", "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE", "settings": "ms-settings:",
}

QUICK_URLS = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "chatgpt": "https://chat.openai.com",
    "stackoverflow": "https://stackoverflow.com",
    "maps": "https://maps.google.com",
}

# ════════════════════════════════════════════════════════════════════════════
#  ACTIVITY LOG + THREAD-SAFE BROADCAST
# ════════════════════════════════════════════════════════════════════════════
activity_log = []
_server_ref = None  # set in PhantronServer.run()


def log(action, detail="", t="info"):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "detail": str(detail)[:200],
        "type": t,
    }
    activity_log.insert(0, entry)
    del activity_log[60:]
    print("[%s] %s: %s" % (t.upper(), action, str(detail)[:70]))
    if _server_ref:
        _server_ref.broadcast_threadsafe({"type": "activity_update", "log": activity_log[:15]})


# ════════════════════════════════════════════════════════════════════════════
#  OPTIONAL LIBS (lazy + safe)
# ════════════════════════════════════════════════════════════════════════════
def pg():
    """Return pyautogui module or None."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except Exception:
        return None


def get_brave():
    for p in BRAVE_PATHS:
        if os.path.exists(p):
            return p
    return None


def open_in_browser(url, new_window=False):
    brave = get_brave()
    if brave:
        args = [brave]
        if new_window:
            args.append("--new-window")
        args.append(url)
        subprocess.Popen(args)
    else:
        webbrowser.open(url)


# ════════════════════════════════════════════════════════════════════════════
#  SCREEN CAPTURE
# ════════════════════════════════════════════════════════════════════════════
def screen_b64():
    p = pg()
    if not p:
        return None
    try:
        img = p.screenshot()
        img = img.resize((640, 360))
        buf = BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=45)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def send_screen():
    if not CONFIG.get("screen_broadcast", True):
        return
    b64 = screen_b64()
    if b64 and _server_ref:
        _server_ref.broadcast_threadsafe({"type": "screen_update", "img": b64})


# ════════════════════════════════════════════════════════════════════════════
#  EXECUTORS  -  the things PHANTRON can actually DO
# ════════════════════════════════════════════════════════════════════════════
def do_open(target):
    t = (target or "").strip()
    if not t:
        return "Kya kholun P7?"
    tl = t.lower()
    log("Khol raha hoon", t, "tool")

    # URL
    if t.startswith("http") or t.startswith("www."):
        url = t if t.startswith("http") else "https://" + t
        open_in_browser(url)
        return t + " khul gaya P7."

    # Quick named site
    if tl in QUICK_URLS:
        open_in_browser(QUICK_URLS[tl])
        return tl.capitalize() + " khul gaya P7."

    # Known app
    if tl in APPS:
        exe = APPS[tl]
        try:
            if exe.endswith(".exe") or exe.endswith(".EXE"):
                if os.path.exists(exe):
                    subprocess.Popen([exe])
                else:
                    subprocess.Popen(exe, shell=True)
            else:
                # protocol / browser path / brave full path
                if os.path.exists(exe):
                    subprocess.Popen([exe])
                elif IS_WINDOWS:
                    os.startfile(exe) if not exe.startswith("ms-") else subprocess.run(
                        ["cmd", "/c", "start", exe], shell=False)
                else:
                    open_in_browser(exe)
            return t + " khul gaya P7."
        except Exception:
            pass

    # Fallback: let the OS resolve it
    try:
        if IS_WINDOWS:
            subprocess.run('start "" "%s"' % t, shell=True, timeout=6)
        else:
            subprocess.Popen([t])
        return t + " khul gaya P7."
    except Exception:
        pass
    try:
        if IS_WINDOWS:
            os.startfile(t)
            return t + " khul gaya P7."
    except Exception as e:
        return "Nahi khul saka: " + str(e)
    return "Nahi khul saka: " + t


def _youtube_first_video_id(query):
    """Fetch the YouTube search page and return the first videoId (reliable, no API)."""
    try:
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PHANTRON/7",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r'"videoId":"([\w-]{11})"', html)
        return m.group(1) if m else None
    except Exception:
        return None


def do_play_song(song):
    song = (song or "").strip()
    if not song:
        song = "trending hindi songs"
    log("Gaana dhundh raha hoon", song, "tool")
    vid = _youtube_first_video_id(song)
    if vid:
        url = "https://www.youtube.com/watch?v=" + vid
        open_in_browser(url, new_window=True)
        log("Gaana chala diya", song, "done")
        send_screen()
        return "'" + song + "' chal raha hai P7."
    # fallback: open search results
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(song)
    open_in_browser(url, new_window=True)
    return "'" + song + "' YouTube pe khol diya P7. Pehla video play karne ke liye click karo."


def do_mouse(action, x=None, y=None, amount=3):
    log("Mouse", action, "tool")
    p = pg()
    if not p:
        return "Mouse control ke liye: pip install pyautogui"
    try:
        w, h = p.size()
        px = int(x) if x is not None else w // 2
        py = int(y) if y is not None else h // 2
        action = (action or "click").lower()
        if action == "click":
            p.moveTo(px, py, duration=0.3); p.click(); send_screen()
            return "Click kiya %d,%d" % (px, py)
        if action == "move":
            p.moveTo(px, py, duration=0.4); send_screen()
            return "Mouse moved %d,%d" % (px, py)
        if action == "double_click":
            p.doubleClick(px, py); send_screen(); return "Double click."
        if action == "right_click":
            p.rightClick(px, py); send_screen(); return "Right click."
        if action == "scroll_up":
            p.scroll(int(amount) * 100); return "Upar scroll."
        if action == "scroll_down":
            p.scroll(-int(amount) * 100); return "Neeche scroll."
        return "Unknown mouse action: " + action
    except Exception as e:
        return "Error: " + str(e)


def do_keyboard(action, text="", keys=None):
    log("Keyboard", str(text or keys)[:40], "tool")
    p = pg()
    if not p:
        return "Keyboard control ke liye: pip install pyautogui"
    try:
        time.sleep(0.25)
        action = (action or "").lower()
        if action == "type":
            p.write(str(text), interval=0.03); return "Type kiya."
        if action == "press":
            p.press(str(text)); return str(text) + " press ki."
        if action == "hotkey" and keys:
            p.hotkey(*[str(k) for k in keys])
            return "+".join(str(k) for k in keys) + " chalaya."
        return "OK"
    except Exception as e:
        return "Error: " + str(e)


def do_command(cmd, shell="powershell"):
    cmd = (cmd or "").strip()
    if not cmd:
        return "Konsa command P7?"
    log("Command", cmd[:60], "tool")
    try:
        if IS_WINDOWS:
            if shell == "cmd":
                args = ["cmd", "/c", cmd]
            else:
                args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
        else:
            args = ["bash", "-lc", cmd]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:2000] if out else "Ho gaya P7."
    except subprocess.TimeoutExpired:
        return "Command timeout ho gaya."
    except Exception as e:
        return "Error: " + str(e)


def do_media(action):
    action = (action or "play_pause").lower()
    keymap = {
        "play_pause": "playpause", "play": "playpause", "pause": "playpause",
        "next": "nexttrack", "previous": "prevtrack", "prev": "prevtrack",
        "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
    }
    k = keymap.get(action)
    if not k:
        return "Pata nahi: " + action
    log("Media", action, "tool")
    p = pg()
    if p:
        try:
            p.press(k); return action + " ho gaya."
        except Exception:
            pass
    # Windows fallback via SendKeys
    vk = {"playpause": 179, "nexttrack": 176, "prevtrack": 177,
          "volumeup": 175, "volumedown": 174, "volumemute": 173}.get(k)
    if vk and IS_WINDOWS:
        do_command("(New-Object -ComObject WScript.Shell).SendKeys([char]%d)" % vk)
        return action + " ho gaya."
    return "Media control unavailable."


def do_system_info():
    if not psutil:
        return "psutil missing: pip install psutil"
    try:
        m = psutil.virtual_memory()
        b = psutil.sensors_battery()
        lines = ["CPU: %s%%" % psutil.cpu_percent(interval=0.6),
                 "RAM: %s%% (%.1fGB / %.1fGB)" % (m.percent, m.used / 1073741824, m.total / 1073741824)]
        if b:
            lines.append("Battery: %d%% (%s)" % (int(b.percent), "Charging" if b.power_plugged else "On battery"))
        procs = sorted(psutil.process_iter(["name", "cpu_percent"]),
                       key=lambda x: x.info.get("cpu_percent") or 0, reverse=True)[:5]
        lines.append("Top: " + ", ".join(p.info.get("name", "?") for p in procs))
        return "\n".join(lines)
    except Exception as e:
        return "Error: " + str(e)


def do_screenshot():
    p = pg()
    if not p:
        return "Screenshot ke liye: pip install pyautogui"
    try:
        path = files_dir() / ("screenshot_%d.png" % int(time.time()))
        p.screenshot(str(path))
        send_screen()
        return "Screenshot save ki: " + str(path)
    except Exception as e:
        return "Error: " + str(e)


def do_create_file(path, content=""):
    try:
        p = Path(path)
        if not p.is_absolute():
            p = files_dir() / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        log("File bana di", str(p), "done")
        return "File ban gayi: " + str(p)
    except Exception as e:
        return "Error: " + str(e)


def do_write_code(description, filename=""):
    """Ask the AI to generate code for `description`, save it, and open it."""
    log("Code likh raha hoon", description[:50], "tool")
    if not filename:
        safe = re.sub(r"[^a-z0-9_]+", "_", description.lower())[:30].strip("_") or "script"
        filename = safe + ".py"
    code = _ai_generate_code(description, filename)
    if not code:
        return "AI se code generate nahi hua. Ollama/Claude check karo P7."
    path = projects_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    log("Code save kiya", str(path), "done")
    try:
        if IS_WINDOWS:
            subprocess.Popen('start "" "%s"' % str(path), shell=True)
    except Exception:
        pass
    return "Code likh diya P7: " + str(path)


def do_web_search(query):
    query = (query or "").strip()
    if not query:
        return "Kya search karun P7?"
    log("Search", query, "tool")
    try:
        req = urllib.request.Request(
            "https://api.duckduckgo.com/?q=%s&format=json&no_html=1" % urllib.parse.quote_plus(query),
            headers={"User-Agent": "PHANTRON/7"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        out = []
        if data.get("AbstractText"):
            out.append(data["AbstractText"][:300])
        if data.get("Answer"):
            out.append("Jawab: " + str(data["Answer"]))
        for tpc in data.get("RelatedTopics", [])[:2]:
            if isinstance(tpc, dict) and tpc.get("Text"):
                out.append("- " + tpc["Text"][:120])
        if out:
            return "\n".join(out)
    except Exception:
        pass
    open_in_browser("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return "'" + query + "' browser me search kar diya P7."


def do_scaffold_project(kind, name=""):
    """Create a starter folder structure for heavier projects."""
    kind = (kind or "").lower().strip()
    name = (name or "P7Project").strip().replace(" ", "_")
    base = projects_dir() / name
    log("Project scaffold", kind + " / " + name, "tool")
    try:
        if kind in ("android", "android studio"):
            for d in ["app/src/main/java", "app/src/main/res/layout", "app/src/main/res/values", "gradle/wrapper"]:
                (base / d).mkdir(parents=True, exist_ok=True)
            (base / "settings.gradle").write_text('rootProject.name = "%s"\ninclude ":app"\n' % name, encoding="utf-8")
            (base / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    package="com.p7.%s">\n  <application android:label="%s"/>\n</manifest>\n'
                % (name.lower(), name), encoding="utf-8")
        elif kind in ("unreal", "unreal engine", "ue5"):
            for d in ["Content", "Config", "Source/%s" % name]:
                (base / d).mkdir(parents=True, exist_ok=True)
            (base / (name + ".uproject")).write_text(
                json.dumps({"FileVersion": 3, "EngineAssociation": "5.4",
                            "Modules": [{"Name": name, "Type": "Runtime", "LoadingPhase": "Default"}]}, indent=2),
                encoding="utf-8")
        elif kind in ("maya", "3d"):
            for d in ["scenes", "scripts", "sourceimages", "renders"]:
                (base / d).mkdir(parents=True, exist_ok=True)
            (base / "workspace.mel").write_text('workspace -fr "scene" "scenes";\n', encoding="utf-8")
        elif kind in ("web", "website", "react"):
            (base / "src").mkdir(parents=True, exist_ok=True)
            (base / "index.html").write_text(
                "<!doctype html><html><head><meta charset='utf-8'><title>%s</title></head>"
                "<body><h1>%s</h1><script src='src/main.js'></script></body></html>" % (name, name),
                encoding="utf-8")
            (base / "src" / "main.js").write_text("console.log('%s ready');\n" % name, encoding="utf-8")
        elif kind in ("ml", "ai", "model", "machine learning"):
            (base / "data").mkdir(parents=True, exist_ok=True)
            (base / "train.py").write_text(
                "# %s - ML training template\n"
                "# pip install torch scikit-learn pandas\n\n"
                "def main():\n    print('TODO: load data, build model, train')\n\n"
                "if __name__ == '__main__':\n    main()\n" % name, encoding="utf-8")
            (base / "requirements.txt").write_text("torch\nscikit-learn\npandas\nnumpy\n", encoding="utf-8")
        else:
            (base / "src").mkdir(parents=True, exist_ok=True)
            (base / "README.md").write_text("# %s\n" % name, encoding="utf-8")
        log("Project ready", str(base), "done")
        try:
            if IS_WINDOWS:
                subprocess.Popen('explorer "%s"' % str(base), shell=True)
        except Exception:
            pass
        return "%s project '%s' ban gaya: %s" % (kind or "basic", name, base)
    except Exception as e:
        return "Error: " + str(e)


def do_os_agent(task):
    """Delegate a complex task to the Agentic OS Controller (multi-step + self-fix)."""
    task = (task or "").strip()
    if not task:
        return "Konsa task agent ko du P7?"
    log("Agentic OS task", task[:60], "tool")
    try:
        from phantron_os_controller import AgenticController
    except Exception as e:
        return "OS controller load nahi hua: " + str(e)

    def _on_event(etype, data):
        # Mirror agent progress into PHANTRON's live activity feed.
        msg = data.get("message") or data.get("description") or ""
        if etype == "step_start":
            log("Step %s" % data.get("index", "?"), data.get("command", "")[:70], "tool")
        elif etype == "step_result":
            log("Step %s %s" % (data.get("index", "?"), "OK" if data.get("ok") else "FAIL"),
                data.get("reason", ""), "done" if data.get("ok") else "error")
        elif etype == "fixing":
            log("Self-fix", msg, "info")
        elif etype == "blocked":
            log("Blocked", msg, "error")
        elif etype in ("plan", "done", "error"):
            log("Agent " + etype, msg, "done" if etype == "done" else "info")
        if _server_ref and msg and etype in ("plan", "step_start", "fixing", "done", "error", "blocked"):
            _server_ref.broadcast_threadsafe({"type": "ai_step", "message": "[agent] " + msg})

    try:
        controller = AgenticController(config=CONFIG, ai_fn=_ai_raw, on_event=_on_event)
        report = controller.process(task)
        ok = report.get("ok")
        summary = report.get("summary", "")
        head = "Task complete P7. " if ok else "Task adhura raha P7. "
        if report.get("error"):
            return head + summary + " (" + str(report["error"]) + ")"
        return head + summary
    except Exception as e:
        return "Agent error: " + str(e)


def do_security_audit(arg=""):
    """Run PHANTRON's defensive Security Engineering & System Auditing Framework."""
    log("Security audit", arg[:50] if arg else "full scan", "tool")
    try:
        from phantron_security_audit import SecurityAuditFramework
    except Exception as e:
        return "Security framework load nahi hua: " + str(e)

    # treat any existing file paths in the argument as access logs to analyze
    log_paths = [tok for tok in (arg or "").split() if os.path.exists(tok)]

    def _on_event(etype, data):
        msg = data.get("message") or data.get("title") or ""
        if etype == "finding":
            sev = data.get("severity", "INFO")
            log("Finding [%s]" % sev, data.get("title", ""),
                "error" if sev in ("HIGH", "CRITICAL") else "info")
        elif msg:
            log("Audit " + etype, msg, "info")
        if _server_ref and msg:
            _server_ref.broadcast_threadsafe({"type": "ai_step", "message": "[audit] " + msg})

    try:
        fw = SecurityAuditFramework(config={"log_paths": log_paths}, on_event=_on_event)
        report = fw.run_full_audit(log_paths)
        c = report["severity_counts"]
        return ("Security audit complete P7. Overall risk: %s. "
                "Findings: %d (CRITICAL %d, HIGH %d, MEDIUM %d, LOW %d). "
                "Details activity log me hain." % (
                    report["overall_risk"], report["total_findings"],
                    c.get("CRITICAL", 0), c.get("HIGH", 0),
                    c.get("MEDIUM", 0), c.get("LOW", 0)))
    except Exception as e:
        return "Audit error: " + str(e)


# ════════════════════════════════════════════════════════════════════════════
#  VISION  +  AUTONOMOUS AGENT  +  CODE DOCTOR  (the multimodal core)
# ════════════════════════════════════════════════════════════════════════════
_vision_engine = None


def vision():
    """Lazy singleton VisionEngine, or None if the module is unavailable."""
    global _vision_engine
    if _vision_engine is None:
        try:
            from phantron_vision import VisionEngine
            _vision_engine = VisionEngine(config=CONFIG)
        except Exception:
            _vision_engine = False
    return _vision_engine or None


def do_describe_screen():
    """Computer-vision: describe what is currently on screen."""
    log("Vision", "describe screen", "tool")
    v = vision()
    if not v:
        return "Vision module load nahi hua. 'pip install pyautogui Pillow' (aur OCR ke liye pytesseract)."
    send_screen()
    return v.describe(ai_fn=_ai_raw)


def do_read_screen():
    """Computer-vision: OCR the screen and return the raw text."""
    log("Vision", "read screen (OCR)", "tool")
    v = vision()
    if not v:
        return "Vision module load nahi hua P7."
    txt = v.read_text()
    if txt == "__NO_OCR__":
        return "OCR off hai. 'pip install pytesseract' + Tesseract-OCR engine install karo P7."
    if not txt or txt.startswith("__"):
        return "Screen par readable text nahi mila."
    return "Screen par ye text hai P7:\n" + " ".join(txt.split())[:1500]


def do_click_text(target):
    """Computer-vision: find on-screen text/button and click it."""
    target = (target or "").strip().strip("'\"")
    if not target:
        return "Kis text par click karun P7?"
    log("Vision click", target, "tool")
    v = vision()
    if not v:
        return "Click-by-text ke liye vision chahiye (pip install pyautogui pytesseract)."
    hits = v.find_text(target)
    if not hits:
        return "Screen par '%s' nahi mila P7." % target
    best = hits[0]
    res = do_mouse("click", best["cx"], best["cy"])
    return "'%s' par click kiya P7 (%d,%d). %s" % (target, best["cx"], best["cy"], res)


def do_auto_task(goal):
    """Run the fully autonomous, vision-grounded perceive->think->act loop."""
    goal = (goal or "").strip()
    if not goal:
        return "Konsa autonomous goal du P7?"
    log("Autonomous", goal[:60], "tool")
    try:
        from phantron_autonomous import AutonomousAgent
    except Exception as e:
        return "Autonomous core load nahi hua: " + str(e)

    def _on_event(etype, data):
        msg = data.get("message") or ""
        if etype in ("goal", "step_start", "step_result", "blocked", "done", "fixing"):
            log("auto:" + etype, msg, "error" if etype == "blocked" else "info")
        if _server_ref and msg:
            _server_ref.broadcast_threadsafe({"type": "ai_step", "message": "[auto] " + msg})

    try:
        agent = AutonomousAgent(config=CONFIG, ai_fn=_ai_raw, executor=execute_action,
                                vision=vision(), on_event=_on_event)
        report = agent.run(goal)
        head = "Goal complete P7. " if report.get("ok") else "Goal adhura P7. "
        return head + (report.get("summary") or "")
    except Exception as e:
        return "Autonomous error: " + str(e)


def _code_doctor():
    try:
        from phantron_codedoctor import CodeDoctor

        def _on_event(etype, data):
            msg = data.get("message") or ""
            if msg:
                log("doctor:" + etype, msg, "info")
                if _server_ref:
                    _server_ref.broadcast_threadsafe({"type": "ai_step", "message": "[doctor] " + msg})

        return CodeDoctor(config=CONFIG, ai_fn=_ai_raw, on_event=_on_event)
    except Exception:
        return None


def do_code_analyze(path):
    log("Code analyze", path, "tool")
    doc = _code_doctor()
    if not doc:
        return "Code Doctor load nahi hua P7."
    return doc.analyze_summary(path)


def do_code_fix(path, error=""):
    log("Code fix", path, "tool")
    doc = _code_doctor()
    if not doc:
        return "Code Doctor load nahi hua P7."
    return doc.auto_fix(path, error)


def do_refactor(path, goal=""):
    log("Refactor", path, "tool")
    doc = _code_doctor()
    if not doc:
        return "Code Doctor load nahi hua P7."
    return doc.refactor(path, goal)


def do_self_heal(command):
    log("Self-heal", str(command)[:60], "tool")
    doc = _code_doctor()
    if not doc:
        return "Code Doctor load nahi hua P7."
    rep = doc.self_heal(command)
    return rep.get("summary", "Self-heal done.")


# ════════════════════════════════════════════════════════════════════════════
#  FAST LOCAL PARSER  (instant, no AI cost)
# ════════════════════════════════════════════════════════════════════════════
def parse_and_execute(msg):
    ml = msg.lower().strip()

    # ── HIGH-PRIORITY EXPLICIT COMMANDS (checked first so their arguments,
    #    which may contain words like "kholo"/"likho", aren't hijacked) ──

    # AUTONOMOUS MULTIMODAL TASK (vision-grounded perceive->think->act)
    m = re.search(r"^\s*(?:auto|autonomous|agent\s*mode|khud\s+(?:se\s+)?karo)\b[:\-]?\s*(.+)", msg, re.I)
    if m and len(m.group(1).strip()) > 2:
        return do_auto_task(m.group(1).strip()), True

    # CODE DOCTOR: analyze / fix / refactor a source file
    m = re.search(r"(?:analyze|analyse|check)\s+(?:code\s+|file\s+)?(\S+\.\w+)", msg, re.I)
    if m:
        return do_code_analyze(m.group(1).strip().strip("'\"")), True
    m = re.search(r"(?:auto[\s-]*fix|bug\s*fix|fix\s+bug\s+in|theek\s+kar(?:o|do)?)\s+(\S+\.\w+)", msg, re.I)
    if m:
        return do_code_fix(m.group(1).strip().strip("'\"")), True
    m = re.search(r"(?:refactor|saaf\s+kar(?:o|do)?)\s+(\S+\.\w+)\s*(?:taaki|so\s+that|:)?\s*(.*)$", msg, re.I)
    if m:
        return do_refactor(m.group(1).strip().strip("'\""), (m.group(2) or "").strip()), True

    # ── PLAY SONG ──
    for pat in [
        r"(.+?)\s+(?:bajao|chalao|baja\s+do|play\s+kar(?:o|do)?|suna\s+do|sunao)",
        r"(?:bajao|play|sun(?:o|na))\s+(.+)",
        r"(.+?)\s+(?:song|gaana)\s+(?:bajao|chalao|play)",
    ]:
        m = re.search(pat, ml)
        if m:
            song = m.group(1).strip().strip("'\"")
            if len(song) > 1 and song not in ("gaana", "song", "music", "koi", "ek"):
                return do_play_song(song), True
    if re.search(r"\b(koi\s+(?:bhi\s+)?gaana|music\s+chalao|gaana\s+bajao)\b", ml):
        return do_play_song("trending hindi songs"), True

    # ── OPEN APP / URL ──
    m = re.search(r"(?:kholo?|open\s+kar(?:o|do)?|launch\s+kar(?:o|do)?|start\s+kar(?:o|do)?|chalu\s+kar(?:o|do)?)\s+(.+)", ml)
    if m:
        return do_open(m.group(1).strip()), True
    for name in QUICK_URLS:
        if re.search(r"\b" + re.escape(name) + r"\b", ml) and any(
                w in ml for w in ["khol", "open", "launch", "chalu", "start", "jao", "dikhao"]):
            return do_open(name), True
    for name in sorted(APPS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", ml) and any(
                w in ml for w in ["khol", "open", "launch", "chalu", "start"]):
            return do_open(name), True

    # ── MEDIA / VOLUME ──
    if any(x in ml for x in ["volume up", "volume badha", "awaz badha", "aawaz badha", "louder"]):
        return do_media("volume_up"), True
    if any(x in ml for x in ["volume down", "volume kam", "awaz kam", "aawaz kam", "quieter"]):
        return do_media("volume_down"), True
    if any(x in ml for x in ["mute", "chup kar", "awaz band", "aawaz band"]):
        return do_media("mute"), True
    if any(x in ml for x in ["agla gaana", "next song", "next track", "agla track"]):
        return do_media("next"), True
    if any(x in ml for x in ["pichla gaana", "previous song", "prev track"]):
        return do_media("previous"), True
    if any(x in ml for x in ["pause karo", "rok do", "ruk ja", "play pause", "resume karo"]):
        return do_media("play_pause"), True

    # ── SYSTEM INFO ──
    if any(x in ml for x in ["system info", "system status", "cpu", "ram kitni", "battery", "memory status", "pc status"]):
        return do_system_info(), True

    # ── SCREENSHOT ──
    if any(x in ml for x in ["screenshot", "screen shot", "screen capture", "screen ki photo"]):
        return do_screenshot(), True

    # ── VISION: read / describe the screen ──
    if any(x in ml for x in ["screen padho", "read screen", "screen ka text", "screen read",
                             "screen text padho"]):
        return do_read_screen(), True
    if any(x in ml for x in ["screen me kya", "screen pe kya", "screen par kya", "describe screen",
                             "screen dekho", "kya dikh raha", "screen describe", "screen analyze",
                             "what's on screen", "whats on screen", "screen samjho"]):
        return do_describe_screen(), True

    # ── SEARCH ──
    m = re.search(r"(?:search\s+kar(?:o|do)?|dhundo|dhundho|khojo|google\s+kar(?:o|do)?)\s+(.+)", ml)
    if m:
        return do_web_search(m.group(1).strip()), True

    # ── MOUSE / CLICK  (coordinates OR vision click-by-text) ──
    if "click" in ml:
        nums = re.findall(r"\d+", ml)
        if len(nums) >= 2:
            return do_mouse("click", int(nums[0]), int(nums[1])), True
        # vision: "<text> par click karo"  /  "click on <text>"
        m = re.search(r"(.+?)\s+(?:par|pe|button\s+par|wale\s+par)\s+click\s+kar(?:o|do)?", ml)
        if not m:
            m = re.search(r"click\s+(?:kar(?:o|do)?\s+)?(?:on\s+|the\s+)?(.+)$", ml)
        if m:
            tgt = m.group(1).strip().strip("'\"")
            if tgt and tgt not in ("karo", "kar do", "here", "yahan", "yaha"):
                return do_click_text(tgt), True
        return do_mouse("click"), True
    if re.search(r"scroll\s+up|upar\s+scroll", ml):
        return do_mouse("scroll_up"), True
    if re.search(r"scroll\s+down|neeche\s+scroll", ml):
        return do_mouse("scroll_down"), True

    # ── KEYBOARD ──
    m = re.search(r"type\s+kar(?:o|do)?\s+[\"']?(.+?)[\"']?$", ml)
    if m:
        return do_keyboard("type", m.group(1)), True
    if "enter dabao" in ml or "enter press" in ml:
        return do_keyboard("press", "enter"), True
    if "escape dabao" in ml or "esc dabao" in ml:
        return do_keyboard("press", "escape"), True

    # ── SECURITY AUDIT ──
    if any(x in ml for x in ["security audit", "system audit", "audit karo", "security scan",
                             "system scan", "threat scan", "endpoint scan", "vulnerability scan"]):
        return do_security_audit(msg), True

    # ── AGENTIC OS TASK (explicit + heavy build triggers) ──
    m = re.search(r"(?:agent|agentic)\s*[:\-]?\s*(.+)", ml)
    if m and len(m.group(1).strip()) > 2:
        return do_os_agent(m.group(1).strip()), True
    if any(x in ml for x in ["apk banao", "apk build", "android build", "gradlew", "gradle build",
                             "unreal build", "ue5 build", "build cook run", "buildcookrun",
                             "project build kar", "compile kar"]):
        return do_os_agent(msg), True

    # ── PROJECT SCAFFOLD ──
    m = re.search(r"(android|unreal|maya|web|react|ml|machine learning|ai)\s+(?:project|app)\s*(.*?)\s*(?:banao|create\s+kar(?:o|do)?|bana\s+do)?$", ml)
    if m and ("project" in ml or "app" in ml) and any(w in ml for w in ["bana", "create"]):
        kind = m.group(1)
        name = (m.group(2) or "P7Project").strip().strip("'\"") or "P7Project"
        return do_scaffold_project(kind, name), True

    # ── CODE WRITING ──
    m = re.search(r"(?:code\s+likho|likho\s+code|program\s+banao|script\s+banao)\s+(.+)", ml)
    if m:
        return do_write_code(m.group(1).strip()), True

    # ── FILE CREATE ──
    m = re.search(r"(.+?)\s+file\s+(?:banao|bana\s+do|create\s+kar(?:o|do)?)", ml)
    if m:
        return do_create_file(m.group(1).strip().strip("'\"")), True

    return None, False


# ════════════════════════════════════════════════════════════════════════════
#  AI BRAIN  (autonomous tool-calling via JSON)
# ════════════════════════════════════════════════════════════════════════════
ACTION_SYSTEM = (
    "Tum PHANTRON ho, {user} ka FULLY AUTONOMOUS AI assistant (Jarvis/Nova jaisa) "
    "jo device ka POORA control rakhta hai. Tum khud sochkar, kai steps me, kaam complete karte ho.\n"
    "Har turn me SIRF ek JSON object return karo, aur kuch nahi:\n"
    '{{"say": "<chhota Hindi update, 1-2 line, koi emoji nahi>", '
    '"actions": [{{"tool": "<naam>", "args": {{...}}}}], '
    '"done": <true ya false>}}\n'
    "Rules:\n"
    "- Jab tak pura kaam khatam na ho, done=false rakho. Main har action ka RESULT wapas dunga, "
    "phir tum agla step decide karna.\n"
    "- Jab kaam pura ho jaye to done=true aur final reply 'say' me do.\n"
    "Available tools (kuch bhi kar sakte ho):\n"
    "- command {{cmd}}: koi bhi PowerShell command (files, processes, network, install, registry, "
    "service start/stop, shutdown, etc.) - ye sabse powerful tool hai\n"
    "- open {{target}}: app ya url kholo\n"
    "- play_song {{song}} | search {{query}}\n"
    "- type {{text}} | press {{key}} | hotkey {{keys:[...]}}\n"
    "- mouse {{action,x,y}}: click/move/scroll_up/scroll_down/double_click/right_click\n"
    "- media {{action}}: play_pause/next/previous/volume_up/volume_down/mute\n"
    "- screenshot {{}} | system_info {{}}\n"
    "- create_file {{path, content}} | write_code {{description, filename}}\n"
    "- scaffold {{kind, name}}: android/unreal/maya/web/ml\n"
    "- os_agent {{task}}: bhaari/multi-step technical kaam (android apk build, unreal build, "
    "system audit, RAM free karna) - ye khud sub-steps banata hai aur error khud fix karta hai\n"
    "- security_audit {{target}}: defensive security scan (network sockets, process integrity, "
    "log analysis) - access log file ka path bhi de sakte ho\n"
    "- read_screen {{}} | describe_screen {{}}: screen ko vision/OCR se padho ya samjho\n"
    "- click_text {{text}}: screen par dikh raha button/text dhundh kar click karo (coordinates ki zaroorat nahi)\n"
    "- auto_task {{goal}}: vision-grounded autonomous multi-step task - khud screen dekh kar poora karo\n"
    "- code_analyze {{path}} | code_fix {{path, error}} | refactor {{path, goal}}: apne hi codebase ko "
    "analyze/fix/refactor karo (auto backup + compile-check ke saath)\n"
    "Hamesha Hindi me. SIRF valid JSON output karo, aur kuch mat likho."
)

CHAT_SYSTEM = (
    "Tum PHANTRON ho, {user} ke personal AI assistant. Sirf Hindi me jawab do. "
    "Chhote jawab - 1-2 line. Koi emoji nahi. User ko '{user}' kaho."
)


def _http_post_json(url, payload, timeout=30, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ask_ollama_raw(prompt, system):
    try:
        resp = _http_post_json(
            CONFIG.get("ollama_url", DEFAULT["ollama_url"]),
            {"model": CONFIG.get("ollama_model", "llama3"),
             "system": system,
             "prompt": prompt,
             "stream": False,
             "options": {"temperature": 0.6, "num_predict": 300}},
            timeout=40)
        return (resp.get("response") or "").strip()
    except Exception as e:
        return "__ERR__ Ollama: " + str(e)[:80]


def _ask_claude_raw(prompt, system):
    if not CONFIG.get("claude_api_key"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CONFIG["claude_api_key"])
        resp = client.messages.create(
            model=CONFIG.get("claude_model", "claude-haiku-4-5-20251001"),
            max_tokens=600, system=system,
            messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text if resp.content else None
    except Exception as e:
        return "__ERR__ Claude: " + str(e)[:80]


def _ai_raw(prompt, system):
    """Pick the configured backend; fall back gracefully."""
    if CONFIG.get("ai_mode") == "claude" and CONFIG.get("claude_api_key"):
        out = _ask_claude_raw(prompt, system)
        if out and not out.startswith("__ERR__"):
            return out
    out = _ask_ollama_raw(prompt, system)
    if out and not out.startswith("__ERR__"):
        return out
    # last resort: try claude even if not primary
    if CONFIG.get("claude_api_key"):
        c = _ask_claude_raw(prompt, system)
        if c and not c.startswith("__ERR__"):
            return c
    return out  # may be an __ERR__ string


def _ai_generate_code(description, filename):
    lang = "Python"
    if filename.endswith((".js", ".ts")):
        lang = "JavaScript"
    elif filename.endswith((".html", ".htm")):
        lang = "HTML"
    elif filename.endswith((".java",)):
        lang = "Java"
    elif filename.endswith((".cpp", ".c", ".h")):
        lang = "C++"
    system = ("You are an expert %s programmer. Output ONLY raw %s code for the request. "
              "No markdown fences, no explanation, just the code." % (lang, lang))
    out = _ai_raw("Request: " + description, system)
    if not out or out.startswith("__ERR__"):
        return None
    out = re.sub(r"^```[a-zA-Z]*\n?", "", out.strip())
    out = re.sub(r"\n?```$", "", out.strip())
    return out


def _extract_json(text):
    if not text:
        return None
    # try direct
    try:
        return json.loads(text)
    except Exception:
        pass
    # try first balanced {...}
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    try:
                        return json.loads(chunk)
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


_ACTION_DISPATCH = {
    "open": lambda a: do_open(a.get("target") or a.get("app") or a.get("url", "")),
    "play_song": lambda a: do_play_song(a.get("song") or a.get("query", "")),
    "search": lambda a: do_web_search(a.get("query") or a.get("text", "")),
    "type": lambda a: do_keyboard("type", a.get("text", "")),
    "press": lambda a: do_keyboard("press", a.get("key") or a.get("text", "")),
    "hotkey": lambda a: do_keyboard("hotkey", keys=a.get("keys", [])),
    "mouse": lambda a: do_mouse(a.get("action", "click"), a.get("x"), a.get("y"), a.get("amount", 3)),
    "media": lambda a: do_media(a.get("action", "play_pause")),
    "command": lambda a: do_command(a.get("cmd") or a.get("command", "")),
    "screenshot": lambda a: do_screenshot(),
    "system_info": lambda a: do_system_info(),
    "create_file": lambda a: do_create_file(a.get("path", ""), a.get("content", "")),
    "write_code": lambda a: do_write_code(a.get("description", ""), a.get("filename", "")),
    "scaffold": lambda a: do_scaffold_project(a.get("kind", ""), a.get("name", "")),
    "os_agent": lambda a: do_os_agent(a.get("task") or a.get("goal") or a.get("text", "")),
    "security_audit": lambda a: do_security_audit(a.get("target") or a.get("log") or a.get("text", "")),
    "read_screen": lambda a: do_read_screen(),
    "describe_screen": lambda a: do_describe_screen(),
    "click_text": lambda a: do_click_text(a.get("text") or a.get("target") or a.get("query", "")),
    "auto_task": lambda a: do_auto_task(a.get("goal") or a.get("task") or a.get("text", "")),
    "code_analyze": lambda a: do_code_analyze(a.get("path") or a.get("file", "")),
    "code_fix": lambda a: do_code_fix(a.get("path") or a.get("file", ""), a.get("error", "")),
    "refactor": lambda a: do_refactor(a.get("path") or a.get("file", ""),
                                      a.get("goal") or a.get("instruction", "")),
    "self_heal": lambda a: do_self_heal(a.get("command") or a.get("cmd", "")),
}


def execute_action(tool, args):
    fn = _ACTION_DISPATCH.get((tool or "").lower())
    if not fn:
        return ""
    try:
        return fn(args or {})
    except Exception as e:
        return "Action error (%s): %s" % (tool, e)


def _offline_brain(msg):
    """A tiny rule-based brain so PHANTRON stays useful even with NO Ollama/Claude.
    Handles greetings, identity, time/date, simple math, thanks and a help menu.
    Returns a Hindi reply string, or None if it has nothing confident to say."""
    user = CONFIG.get("user_name", "P7")
    name = CONFIG.get("assistant_name", "PHANTRON")
    t = (msg or "").lower().strip()
    if not t:
        return None

    # greetings / small talk
    if re.search(r"\b(hi|hello|hey|namaste|namaskar|salaam|yo)\b", t) or \
       re.search(r"kaise ho|kaisa hai|kya haal|whats up|what's up|sab badhiya", t):
        return "Namaste %s. Main %s, taiyaar hoon. Bataiye kya karna hai?" % (user, name)

    # identity
    if re.search(r"tum kaun|who are you|aap kaun|naam kya|your name|tumhara naam", t):
        return ("Main %s hoon %s - aapka personal AI assistant. App khol sakta hoon, "
                "gaane chala sakta hoon, system control kar sakta hoon aur bahut kuch."
                % (name, user))

    # thanks / acknowledgement
    if re.search(r"thank|thanks|shukriya|dhanyavaad|dhanyawad|good job|shabaash|badhiya", t):
        return "Hamesha %s. Aur kuch?" % user

    # current time
    if re.search(r"\b(time|samay|kitne baje|baj rahe|kya baja)\b", t):
        return "Abhi %s baje hain %s." % (datetime.now().strftime("%I:%M %p"), user)

    # date / day
    if re.search(r"\b(date|tareekh|tarikh|din kaunsa|kaunsa din|today|aaj ki)\b", t):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        now = datetime.now()
        return "Aaj %s, %s hai %s." % (days[now.weekday()], now.strftime("%d %B %Y"), user)

    # simple arithmetic:  e.g. "5 + 3",  "12 * 4",  "100 / 5"
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([+\-*x/])\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
        op = "*" if op == "x" else op
        try:
            if op == "/" and b == 0:
                return "Zero se divide nahi kar sakta %s." % user
            res = {"+": a + b, "-": a - b, "*": a * b, "/": (a / b if b else 0)}[op]
            res = int(res) if float(res).is_integer() else round(res, 4)
            return "%s = %s" % (m.group(0).strip(), res)
        except Exception:
            pass

    # help / capability menu
    if re.search(r"\b(help|madad|kya kar sakte|command|kaam kya|menu|kya kar sakta)\b", t):
        return ("Main ye sab kar sakta hoon %s:\n"
                "- 'chrome kholo' / 'youtube kholo' - app ya website\n"
                "- 'arijit singh tum hi ho bajao' - gaana chalao\n"
                "- 'screenshot lo' | 'system info' | 'volume badha do'\n"
                "- 'search karo <topic>' - web search\n"
                "- 'code likho <kaam>' | 'android project banao'\n"
                "- 'security audit' - defensive system scan\n"
                "Aur khulkar baat-cheet ke liye Ollama ya Claude laga do, phir main aur smart ban jaunga."
                % user)

    return None


def ai_brain(msg):
    """Autonomous AGENTIC mode: AI plans + executes over multiple steps with full control."""
    user = CONFIG.get("user_name", "P7")
    if CONFIG.get("autonomous", True):
        max_steps = max(1, int(CONFIG.get("max_agent_steps", 6)))
        context = "User ne kaha: " + msg
        final_say = ""
        for step in range(max_steps):
            raw = _ai_raw(context, ACTION_SYSTEM.format(user=user))
            if raw and raw.startswith("__ERR__"):
                # No cloud/local model reachable -> use the built-in offline brain
                # for the everyday stuff, otherwise give an actionable hint.
                return _offline_brain(msg) or _ai_offline_hint(raw)
            data = _extract_json(raw)
            if not (data and isinstance(data, dict)):
                # Model didn't return JSON -> treat as a plain reply and stop.
                cleaned = clean(raw)
                return cleaned or (final_say or "Samajh nahi aaya P7.")

            say = (data.get("say") or "").strip()
            actions = data.get("actions") or []
            done = data.get("done", True)
            if say:
                final_say = say
                # live narration so screen shows what PHANTRON is doing
                if _server_ref and (actions or not done):
                    _server_ref.broadcast_threadsafe(
                        {"type": "ai_step", "message": say, "step": step + 1})

            if not actions:
                # nothing to do -> we're done
                return final_say or "Ho gaya P7."

            results = []
            for act in actions:
                if isinstance(act, dict):
                    r = execute_action(act.get("tool", ""), act.get("args", {}))
                    tool = act.get("tool", "?")
                    results.append("[%s] %s" % (tool, str(r)[:300]))

            if done:
                tail = "\n".join(results)
                return (final_say + ("\n" + tail if tail else "")).strip() or "Ho gaya P7."

            # feed results back so the model can plan the next step
            context = (
                "User ne kaha: " + msg +
                "\n\nAb tak kiye gaye steps ke results:\n" + "\n".join(results) +
                "\n\nAgla step decide karo. Agar kaam pura ho gaya to done=true aur final reply do."
            )
        # ran out of steps
        return final_say or "Itne steps me pura nahi hua P7, dobara batao."

    # plain chat mode (autonomous off)
    raw = _ai_raw("P7: " + msg, CHAT_SYSTEM.format(user=user))
    if raw and raw.startswith("__ERR__"):
        return _offline_brain(msg) or _ai_offline_hint(raw)
    return clean(raw) or "Samajh nahi aaya P7, dobara boliye."


def _ai_offline_hint(err=""):
    user = CONFIG.get("user_name", "P7")
    return ("Is baat ka jawab dene ke liye mera AI brain chahiye %s, jo abhi off hai. "
            "Free me chalane ke liye Ollama install karke 'ollama serve' karo (model: "
            "'ollama pull llama3'), ya config.json me apni claude_api_key daal do. "
            "Tab tak app kholna, gaana, screenshot, system info, time/date jaise "
            "commands chalte rahenge." % user)


# ════════════════════════════════════════════════════════════════════════════
#  VOICE  (output + input)  -  all optional
# ════════════════════════════════════════════════════════════════════════════
def clean(text):
    if not text:
        return ""
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        r"\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
        r"\U00002600-\U000026FF\ufe0f\u2640-\u2642]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


_voice_lock = threading.Lock()


def speak(text):
    if not CONFIG.get("voice_output_enabled", True):
        return
    t = clean(text)[:500]
    if t:
        threading.Thread(target=_speak_worker, args=(t,), daemon=True).start()


def _speak_worker(text):
    if not _voice_lock.acquire(blocking=False):
        return
    try:
        engine = CONFIG.get("tts_engine", "auto")
        # 1. Windows SAPI - best Hindi, no download
        if engine in ("auto", "sapi") and IS_WINDOWS:
            try:
                import win32com.client as wc
                sp = wc.Dispatch("SAPI.SpVoice")
                sp.Volume = 100
                sp.Speak(text)
                return
            except Exception:
                pass
        # 2. edge-tts (online, natural male Hindi)
        if engine in ("auto", "edge"):
            if _speak_edge(text):
                return
        # 3. pyttsx3 (offline)
        try:
            import pyttsx3
            e = pyttsx3.init()
            e.setProperty("rate", 165)
            e.say(text)
            e.runAndWait()
            return
        except Exception:
            pass
        print("[PHANTRON-SAY] " + text)
    finally:
        _voice_lock.release()


def _speak_edge(text):
    try:
        import edge_tts
        import tempfile
        tmp = tempfile.mktemp(suffix=".mp3")

        async def _gen():
            c = edge_tts.Communicate(text, voice=CONFIG.get("tts_voice_edge", "hi-IN-MadhurNeural"))
            await c.save(tmp)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_gen())
        finally:
            loop.close()
        try:
            import pygame
            pygame.mixer.init()
            pygame.mixer.music.load(tmp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
            pygame.mixer.quit()
        except Exception:
            if IS_WINDOWS:
                subprocess.run(["powershell", "-c",
                                "(New-Object Media.SoundPlayer);"
                                "Add-Type -AssemblyName presentationCore;"
                                "$p=New-Object system.windows.media.mediaplayer;"
                                "$p.open('%s');$p.Play();Start-Sleep 6" % tmp],
                               capture_output=True, timeout=12)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _recognizer():
    try:
        import speech_recognition as sr
        return sr
    except Exception:
        return None


def listen_once(timeout=8, phrase=20):
    sr = _recognizer()
    if not sr:
        return ""
    try:
        r = sr.Recognizer()
        r.dynamic_energy_threshold = True
        with sr.Microphone() as src:
            r.adjust_for_ambient_noise(src, duration=0.3)
            audio = r.listen(src, timeout=timeout, phrase_time_limit=phrase)
        return r.recognize_google(audio, language=CONFIG.get("stt_language", "hi-IN"))
    except Exception:
        return ""


def start_wake_loop(callback):
    if not CONFIG.get("voice_input_enabled", True):
        print("[VOICE] Voice input disabled in config.")
        return
    sr = _recognizer()
    if not sr:
        print("[VOICE] Mic unavailable (pip install SpeechRecognition pyaudio). Text still works.")
        return

    def _loop():
        r = sr.Recognizer()
        r.dynamic_energy_threshold = True
        wake = CONFIG.get("wake_word", "phantron").lower()
        print("[VOICE] Active - bolo '%s ...' command dene ke liye" % wake.upper())
        try:
            mic = sr.Microphone()
        except Exception as e:
            print("[VOICE] Microphone init fail:", e)
            return
        while True:
            try:
                with mic as src:
                    r.adjust_for_ambient_noise(src, duration=0.2)
                    audio = r.listen(src, timeout=4, phrase_time_limit=7)
                text = r.recognize_google(audio, language=CONFIG.get("stt_language", "hi-IN"))
                low = text.lower()
                if wake in low:
                    cmd = low.split(wake, 1)[1].strip()
                    if not cmd:
                        speak("Haan P7")
                        cmd = listen_once()
                    if cmd and cmd.lower() != wake:
                        print("[VOICE] Command:", cmd)
                        callback(cmd)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True).start()


# ════════════════════════════════════════════════════════════════════════════
#  CAPABILITY SELF-CHECK
# ════════════════════════════════════════════════════════════════════════════
def _module_present(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _ollama_online():
    """Quick, cheap probe: is an Ollama server actually answering locally?"""
    try:
        base = (CONFIG.get("ollama_url") or "").split("/api/")[0] or "http://localhost:11434"
        req = urllib.request.Request(base + "/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def detect_capabilities():
    """Figure out what PHANTRON can actually do right now, so the user is never
    left guessing why something 'does nothing'."""
    if CONFIG.get("claude_api_key"):
        ai_ready, ai_detail = True, "Claude (cloud)"
    elif _ollama_online():
        ai_ready, ai_detail = True, "Ollama %s" % CONFIG.get("ollama_model", "llama3")
    else:
        ai_ready, ai_detail = False, "OFFLINE (set up Ollama or claude_api_key)"

    return {
        "ai_ready": ai_ready,
        "ai_detail": ai_detail,
        "metrics": _module_present("psutil"),       # CPU/RAM/battery gauges
        "automation": _module_present("pyautogui"),  # mouse/keyboard/screenshot/screen
        "vision_ocr": _module_present("pytesseract"),  # read screen text / click-by-text
        "vision_match": _module_present("cv2"),        # robust image template matching
        "voice_out": (_module_present("pyttsx3") or _module_present("win32com")
                      or _module_present("edge_tts")),
        "voice_in": _module_present("speech_recognition"),
    }


CAPABILITIES = {}


def capability_summary(caps):
    def mark(ok):
        return "ON " if ok else "off"
    return [
        "  AI brain   : %s  (%s)" % (mark(caps["ai_ready"]), caps["ai_detail"]),
        "  Metrics    : %s  (psutil - CPU/RAM/battery gauges)" % mark(caps["metrics"]),
        "  Automation : %s  (pyautogui - mouse/keyboard/screenshot/live screen)" % mark(caps["automation"]),
        "  Vision OCR : %s  (pytesseract - read screen text / click-by-text)" % mark(caps.get("vision_ocr")),
        "  Voice out  : %s  (spoken replies)" % mark(caps["voice_out"]),
        "  Voice in   : %s  (microphone)" % mark(caps["voice_in"]),
    ]


def capability_interface_note(caps):
    """A short human note PHANTRON shows inside the web interface on connect."""
    parts = []
    if not caps["ai_ready"]:
        parts.append("AI brain OFFLINE - Ollama/Claude lagao to khulkar baat ho. "
                     "Tab tak app/gaana/screenshot/time jaise commands chalenge.")
    if not caps["metrics"]:
        parts.append("CPU/RAM gauges ke liye 'pip install psutil' chahiye.")
    if not caps["automation"]:
        parts.append("Screenshot/mouse/keyboard/live-screen ke liye 'pip install pyautogui Pillow' chahiye.")
    if not parts:
        return "Sab systems ONLINE. Bataiye kya karna hai %s." % CONFIG.get("user_name", "P7")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════════════════════
#  SERVER
# ════════════════════════════════════════════════════════════════════════════
class PhantronServer:
    def __init__(self):
        self.clients = set()
        self.loop = None

    # ---- broadcasting ----
    async def broadcast(self, msg):
        if not self.clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        await asyncio.gather(*[c.send(data) for c in list(self.clients)], return_exceptions=True)

    def broadcast_threadsafe(self, msg):
        """Safe to call from ANY thread."""
        if self.loop and not self.loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)
            except Exception:
                pass

    # ---- core processing ----
    def process_sync(self, msg):
        """Runs in a worker thread (executors are blocking)."""
        result, handled = parse_and_execute(msg)
        if handled:
            return result
        return ai_brain(msg)

    async def process_msg(self, msg):
        return await self.loop.run_in_executor(None, self.process_sync, msg)

    async def _respond(self, msg, source="user"):
        await self.broadcast({"type": "user_message", "message": msg, "source": source})
        await self.broadcast({"type": "thinking", "state": True})
        log("Command", msg[:60], "info")
        try:
            resp = await self.process_msg(msg)
        except Exception as e:
            resp = "Error: " + str(e)
        await self.broadcast({"type": "thinking", "state": False})
        await self.broadcast({"type": "ai_response", "message": resp})
        await self.broadcast({"type": "activity_update", "log": activity_log[:15]})
        speak(resp)
        return resp

    # ---- websocket handler (path optional for version compatibility) ----
    async def handle(self, ws, path=None):
        self.clients.add(ws)
        print("[WS] Client connected (total %d)" % len(self.clients))
        await ws.send(json.dumps({
            "type": "system",
            "message": "%s ACTIVE. %s, hamesha taiyaar hoon." % (
                CONFIG.get("assistant_name", "PHANTRON"), CONFIG.get("user_name", "P7")),
            "status": "online",
        }, ensure_ascii=False))
        await ws.send(json.dumps({
            "type": "capabilities",
            "caps": CAPABILITIES,
            "ai_online": bool(CAPABILITIES.get("ai_ready")),
        }, ensure_ascii=False))
        await ws.send(json.dumps({
            "type": "system",
            "message": capability_interface_note(CAPABILITIES),
        }, ensure_ascii=False))
        await ws.send(json.dumps({"type": "activity_update", "log": activity_log[:15]}, ensure_ascii=False))
        try:
            async for raw in ws:
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                msg = (d.get("message") or "").strip()
                if not msg:
                    continue
                await self._respond(msg, source=d.get("source", "interface"))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)
            print("[WS] Client disconnected (total %d)" % len(self.clients))

    # ---- background loops ----
    async def metrics_loop(self):
        interval = CONFIG.get("metrics_interval_sec", 2)
        while True:
            try:
                if self.clients and psutil:
                    m = psutil.virtual_memory()
                    b = psutil.sensors_battery()
                    await self.broadcast({
                        "type": "metrics",
                        "cpu": psutil.cpu_percent(interval=None),
                        "ram": m.percent,
                        "ram_used": "%.1fGB" % (m.used / 1073741824),
                        "ram_total": "%.0fGB" % (m.total / 1073741824),
                        "battery": int(b.percent) if b else 100,
                        "charging": bool(b.power_plugged) if b else True,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "os": platform.system() + " " + platform.release(),
                    })
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def screen_loop(self):
        if not CONFIG.get("screen_broadcast", True):
            return
        interval = CONFIG.get("screen_interval_sec", 4)
        while True:
            try:
                if self.clients:
                    b64 = await self.loop.run_in_executor(None, screen_b64)
                    if b64:
                        await self.broadcast({"type": "screen_update", "img": b64})
            except Exception:
                pass
            await asyncio.sleep(interval)

    # ---- terminal + voice input ----
    def start_console_input(self):
        def _loop():
            print("[CONSOLE] Type a command and press Enter (or 'exit' to quit):")
            while True:
                try:
                    line = sys.stdin.readline()
                except Exception:
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                if line.lower() in ("exit", "quit", "band karo"):
                    print("[PHANTRON] Bye P7.")
                    os._exit(0)
                asyncio.run_coroutine_threadsafe(self._respond(line, source="console"), self.loop)
        threading.Thread(target=_loop, daemon=True).start()

    def voice_callback(self, cmd):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._respond(cmd, source="voice"), self.loop)

    # ---- main ----
    async def run(self):
        global _server_ref
        _server_ref = self
        self.loop = asyncio.get_running_loop()
        port = CONFIG.get("ws_port", 8765)

        global CAPABILITIES
        CAPABILITIES = detect_capabilities()

        bar = "=" * 54
        print(bar)
        print("  PHANTRON v7  -  %s BEAST MODE" % CONFIG.get("user_name", "P7"))
        print(bar)
        print("  Mode     : %s (autonomous=%s)" % (CONFIG.get("ai_mode"), CONFIG.get("autonomous")))
        print("  Local AI : Ollama %s" % CONFIG.get("ollama_model"))
        print("  Cloud AI : Claude %s" % ("ON" if CONFIG.get("claude_api_key") else "off"))
        print("  Inputs   : Interface + Terminal + Voice")
        print("  Port     : %d" % port)
        print(bar)
        print("  CAPABILITY CHECK")
        for line in capability_summary(CAPABILITIES):
            print(line)
        if not CAPABILITIES.get("ai_ready"):
            print("  NOTE: AI brain OFFLINE. App/gaana/screenshot/system/time jaise")
            print("        commands chalenge. Khulkar baat-cheet ke liye Ollama")
            print("        (ollama serve) ya config.json me claude_api_key lagao.")
        if not (CAPABILITIES.get("metrics") and CAPABILITIES.get("automation")):
            print("  TIP : Missing features ke liye:  pip install -r requirements.txt")
        print(bar)

        asyncio.create_task(self.metrics_loop())
        asyncio.create_task(self.screen_loop())
        self.start_console_input()
        start_wake_loop(self.voice_callback)

        if CONFIG.get("open_interface", True):
            iface = Path(__file__).parent / "Interface.html"
            if iface.exists():
                try:
                    if IS_WINDOWS:
                        subprocess.Popen(["cmd", "/c", "start", "", str(iface)], shell=False)
                    else:
                        webbrowser.open(iface.as_uri())
                except Exception:
                    pass

        async with websockets.serve(self.handle, "localhost", port):
            print("[PHANTRON] READY  ->  ws://localhost:%d" % port)
            print("[PHANTRON] Bolo, type karo, ya interface use karo. API ke bina bhi chalega!")
            await asyncio.Future()  # run forever


def main():
    server = PhantronServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[PHANTRON] Band ho gaya. Bye P7.")
    except OSError as e:
        if "10048" in str(e) or "address already in use" in str(e).lower():
            print("\n[ERROR] Port %d busy hai. Pehle wala PHANTRON band karo." % CONFIG.get("ws_port", 8765))
        else:
            print("\n[ERROR]", e)
        try:
            input("Enter dabao band karne ke liye...")
        except Exception:
            pass


if __name__ == "__main__":
    main()
