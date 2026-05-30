# -*- coding: utf-8 -*-
"""PHANTRON lite - single-file voice+text assistant for P7 (Windows)."""

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
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
HERE = Path(__file__).parent

CFG = {
    "ai_mode": "ollama",
    "claude_api_key": "",
    "claude_model": "claude-haiku-4-5-20251001",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434/api/generate",
    "user_name": "P7",
    "wake_word": "phantron",
    "voice_output": True,
    "voice_input": True,
    "stt_language": "hi-IN",
}
_cfgfile = HERE / "config.json"
if _cfgfile.exists():
    try:
        CFG.update(json.load(open(_cfgfile, encoding="utf-8")))
    except Exception:
        pass

APPS = {
    "chrome": "chrome.exe", "brave": "brave.exe", "firefox": "firefox.exe",
    "edge": "msedge.exe", "notepad": "notepad.exe", "vscode": "code.exe",
    "code": "code.exe", "spotify": "Spotify.exe", "discord": "Discord.exe",
    "vlc": "vlc.exe", "explorer": "explorer.exe", "calculator": "calc.exe",
    "calc": "calc.exe", "paint": "mspaint.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe", "word": "WINWORD.EXE", "excel": "EXCEL.EXE",
}
URLS = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "github": "https://github.com", "gmail": "https://mail.google.com",
}


def say_log(msg):
    print("[PHANTRON] " + str(msg))


def pg():
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except Exception:
        return None


def open_browser(url):
    webbrowser.open(url)


def do_open(target):
    t = (target or "").strip()
    if not t:
        return "Kya kholun P7?"
    tl = t.lower()
    if t.startswith("http") or t.startswith("www."):
        open_browser(t if t.startswith("http") else "https://" + t)
        return t + " khul gaya P7."
    if tl in URLS:
        open_browser(URLS[tl])
        return tl + " khul gaya P7."
    exe = APPS.get(tl, t)
    try:
        if IS_WINDOWS:
            subprocess.Popen('start "" "%s"' % exe, shell=True)
        else:
            subprocess.Popen([exe])
        return t + " khul gaya P7."
    except Exception as e:
        return "Nahi khula: " + str(e)


def do_command(cmd):
    cmd = (cmd or "").strip()
    if not cmd:
        return "Konsa command P7?"
    try:
        if IS_WINDOWS:
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
        else:
            args = ["bash", "-lc", cmd]
        r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:1500] if out else "Ho gaya P7."
    except Exception as e:
        return "Error: " + str(e)


def do_play(song):
    song = (song or "trending hindi songs").strip()
    try:
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(song)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r'"videoId":"([\w-]{11})"', html)
        if m:
            open_browser("https://www.youtube.com/watch?v=" + m.group(1))
            return "'" + song + "' chal raha hai P7."
    except Exception:
        pass
    open_browser("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(song))
    return "'" + song + "' YouTube pe khol diya P7."


def do_media(action):
    keymap = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
              "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}
    k = keymap.get(action)
    if not k:
        return "Pata nahi: " + action
    p = pg()
    if p:
        try:
            p.press(k)
            return action + " ho gaya."
        except Exception:
            pass
    return "Media control ke liye pyautogui chahiye."


def do_system_info():
    try:
        import psutil
    except Exception:
        return "psutil missing: pip install psutil"
    m = psutil.virtual_memory()
    b = psutil.sensors_battery()
    s = "CPU: %s%% | RAM: %s%%" % (psutil.cpu_percent(interval=0.5), m.percent)
    if b:
        s += " | Battery: %d%%" % int(b.percent)
    return s


def do_screenshot():
    p = pg()
    if not p:
        return "Screenshot ke liye: pip install pyautogui"
    path = str(HERE / ("screenshot_%d.png" % int(time.time())))
    p.screenshot(path)
    return "Screenshot: " + path


def do_search(q):
    open_browser("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
    return "'" + q + "' search kar diya P7."


def parse_cmd(msg):
    ml = msg.lower().strip()
    m = re.search(r"(?:kholo?|open|launch|start)\s+(.+)", ml)
    if m:
        return do_open(m.group(1).strip())
    for n in list(URLS) + list(APPS):
        if n in ml and any(w in ml for w in ["khol", "open", "launch", "start"]):
            return do_open(n)
    m = re.search(r"(.+?)\s+(?:bajao|chalao|play)", ml)
    if m and m.group(1).strip() not in ("gaana", "song", "music"):
        return do_play(m.group(1).strip())
    if "gaana" in ml or "song" in ml or "music" in ml:
        return do_play("trending hindi songs")
    if any(x in ml for x in ["volume up", "awaz badha", "louder"]):
        return do_media("volume_up")
    if any(x in ml for x in ["volume down", "awaz kam"]):
        return do_media("volume_down")
    if "mute" in ml:
        return do_media("mute")
    if any(x in ml for x in ["pause", "rok", "resume"]):
        return do_media("play_pause")
    if "next" in ml or "agla gaana" in ml:
        return do_media("next")
    if any(x in ml for x in ["system", "cpu", "ram", "battery", "status"]):
        return do_system_info()
    if "screenshot" in ml or "screen shot" in ml:
        return do_screenshot()
    m = re.search(r"(?:search|dhundo|khojo)\s+(.+)", ml)
    if m:
        return do_search(m.group(1).strip())
    m = re.search(r"(?:command|cmd|run)\s+(.+)", ml)
    if m:
        return do_command(m.group(1).strip())
    return None


def ask_ai(msg):
    sysmsg = ("Tum PHANTRON ho, %s ke AI assistant. Sirf Hindi me, 1-2 line, "
              "koi emoji nahi." % CFG["user_name"])
    if CFG.get("ai_mode") == "claude" and CFG.get("claude_api_key"):
        try:
            import anthropic
            c = anthropic.Anthropic(api_key=CFG["claude_api_key"])
            r = c.messages.create(model=CFG["claude_model"], max_tokens=300,
                                  system=sysmsg, messages=[{"role": "user", "content": msg}])
            return r.content[0].text if r.content else "Samajh nahi aaya P7."
        except Exception as e:
            return "Claude error: " + str(e)[:60]
    try:
        data = json.dumps({"model": CFG["ollama_model"], "system": sysmsg,
                           "prompt": msg, "stream": False,
                           "options": {"num_predict": 200}}).encode()
        req = urllib.request.Request(CFG["ollama_url"], data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return (json.loads(r.read().decode()).get("response") or "").strip() or "..."
    except Exception:
        return "AI offline P7. Ollama start karo (ollama serve) ya claude_api_key daalo."


def handle(msg):
    out = parse_cmd(msg)
    if out is None:
        out = ask_ai(msg)
    say_log(msg + "  ->  " + str(out))
    speak(out)
    return out


_spk = threading.Lock()


def speak(text):
    if not CFG.get("voice_output", True) or not text:
        return

    def _w():
        if not _spk.acquire(blocking=False):
            return
        try:
            t = re.sub(r"\s+", " ", str(text)).strip()[:400]
            if IS_WINDOWS:
                try:
                    import win32com.client as wc
                    wc.Dispatch("SAPI.SpVoice").Speak(t)
                    return
                except Exception:
                    pass
            try:
                import pyttsx3
                e = pyttsx3.init()
                e.say(t)
                e.runAndWait()
            except Exception:
                pass
        finally:
            _spk.release()

    threading.Thread(target=_w, daemon=True).start()


def voice_loop():
    if not CFG.get("voice_input", True):
        return
    try:
        import speech_recognition as sr
    except Exception:
        say_log("Voice off (pip install SpeechRecognition pyaudio). Text chalega.")
        return
    r = sr.Recognizer()
    wake = CFG["wake_word"].lower()
    say_log("Voice active - bolo '%s ...'" % wake.upper())
    try:
        mic = sr.Microphone()
    except Exception as e:
        say_log("Mic fail: " + str(e))
        return
    while True:
        try:
            with mic as src:
                r.adjust_for_ambient_noise(src, duration=0.2)
                audio = r.listen(src, timeout=5, phrase_time_limit=7)
            text = r.recognize_google(audio, language=CFG["stt_language"]).lower()
            if wake in text:
                cmd = text.split(wake, 1)[1].strip()
                if cmd:
                    handle(cmd)
        except Exception:
            pass


def main():
    print("=" * 44)
    print("  PHANTRON lite - %s" % CFG["user_name"])
    print("  Type command + Enter, ya '%s ...' bolo" % CFG["wake_word"].upper())
    print("  'exit' to quit")
    print("=" * 44)
    threading.Thread(target=voice_loop, daemon=True).start()
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "band karo"):
            break
        handle(line)
    print("[PHANTRON] Bye P7.")


if __name__ == "__main__":
    main()
