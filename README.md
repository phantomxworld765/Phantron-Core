# PHANTRON v2

P7's personal, Jarvis-style AI assistant for Windows. Voice + text + a
cyberpunk web UI, with the power to open apps, search the web, control media,
automate the mouse/keyboard, run commands, manage files — and actually *think*
and *chat* through a real LLM brain that runs on **your** machine, under
**your** rules.

> The core runs on the Python standard library alone. No pip install is
> required just to boot. Extras (voice, automation) light up automatically
> when their libraries are present and are skipped gracefully when not.

---

## Quick start

```bat
:: 1) clone, then from the repo folder:
python -m phantron
```

That's it — the cyberpunk UI opens in your browser at `http://127.0.0.1:8770`.
On Windows you can also just double-click **`run.bat`**.

Useful flags:

```bat
python -m phantron            :: web UI (default)
python -m phantron --voice    :: also listen for the wake word "phantron"
python -m phantron --cli      :: text chat in the terminal
python -m phantron --status   :: show brain/voice status and exit
```

---

## Give it a brain (so it can think + chat)

PHANTRON talks to whatever you configure in `config.json` (created on first
run). You get both worlds — **online** and **offline**. Pick one:

### ONLINE — OpenRouter (primary, recommended)
One key, hundreds of models including free ones.
1. Get a key at https://openrouter.ai/keys
2. In `config.json`:
```json
"brain": {
  "provider": "openrouter",
  "openrouter": {
    "api_key": "YOUR_OPENROUTER_KEY",
    "model": "meta-llama/llama-3.1-8b-instruct:free"
  }
}
```
3. Or just leave `"provider": "auto"` — if a key is present it's used first.

Other free model ids to try: `google/gemini-2.0-flash-exp:free`,
`deepseek/deepseek-chat`. You can also pass the key via env `OPENROUTER_API_KEY`.

### OFFLINE — Ollama (free, private, no internet)
1. Install Ollama: https://ollama.com
2. Pull a model: `ollama pull llama3.1`  (or `qwen2.5`, `mistral`, …)
3. In `config.json` set `"brain": { "provider": "ollama" }` (or leave `"auto"`).

No limits, no cost, runs entirely on your PC.

### Other online options
- **OpenAI / Groq / LM Studio** — fill `brain.openai` (base_url + api_key + model).
- **Claude** — fill `brain.anthropic.api_key` (or env `ANTHROPIC_API_KEY`).

If no brain is configured, PHANTRON still runs in **command mode** (open apps,
play songs, system info, etc.) via a built-in rule parser.

---

## Voice — clean Hindi + English (no accent mixing)

PHANTRON detects Hindi (Devanagari) vs English (Latin) in every sentence and
speaks each part with its **own** voice, so the accents never blend.

1. See your installed voices: `python -m phantron --voices`
2. Put a name fragment in `config.json`:
```json
"voice": {
  "voice_hindi": "Kalpana",   // or "Hemant", "Swara", …
  "voice_english": "David",   // or "Zira", "Mark", …
  "mixed_speech": true
}
```
Leave a field empty to auto-pick the best installed match. On Windows you can
add more voices via *Settings → Time & Language → Speech*.

For input, offline speech-to-text uses **Vosk** when `voice.vosk_model` points
to a downloaded model folder; otherwise free Google web STT is used.

---

## Optional extras

```bat
pip install -r requirements.txt
```

| Feature                       | Needs                              |
|-------------------------------|------------------------------------|
| System info (CPU/RAM/battery) | `psutil`                           |
| Screenshots, mouse/keyboard   | `pyautogui`, `Pillow`              |
| Voice input (mic → text)      | `SpeechRecognition`, `pyaudio`     |
| Voice output (text → speech)  | `pyttsx3` (and `pywin32` on Win)   |

If `pyaudio` won't install on Windows:
`pip install pipwin && pipwin install pyaudio`.

---

## What it can do (tools the brain can use)

`open_app`, `play_song`, `web_search`, `system_info`, `media`, `screenshot`,
`mouse`, `type_text`, `press_key`, `create_file`, `read_file`, `list_dir`,
`run_command`, `tell_time`, `see_screen`, `read_screen`, `click_text`,
`remember`, `recall`, `forget`, `clipboard`, `hotkey`, `window`, `write_in`,
`set_reminder`, `timer`, `add_note`, `read_notes`, `add_todo`, `list_todo`,
`weather`, `news`, `calc`, `close_app`, `list_processes`, `lock_pc`, `power`,
`volume`, `open_folder`, `security_audit`, `antivirus_scan`, `firewall`,
`open_ports`, `scan_suspicious`, `quarantine_file`, `send_whatsapp`,
`send_telegram`, `send_email`, `read_email`, `heal_code`, `fix_screen_error`,
`schedule_reminder`, `find_files`, `backup_folder`, `set_voice_passphrase`.

In **autonomous** mode the brain decides which tools to call and can chain a
few of them to finish a request, then replies to you.

### Productivity (work even offline, no API key)
- **Reminders & timers** — "remind me to drink water in 10 min", "set timer for
  5 minutes". PHANTRON speaks + shows it when the time is up. Reminders are
  **saved to disk** and re-armed automatically after a restart.
- **Notes & to-do** — "note down buy milk", "add todo finish report",
  "read notes", "list todo". Saved to `~/Phantron/Files`.
- **Calculator** — "calculate 25*4+10" or just "12+8*2".
- **Weather & news** — "weather in Delhi", "news about technology" (free, no key).
- **System** — "lock pc", "running processes", "close chrome", "open downloads".
- **Export** — "export chat" saves the whole conversation to a file.

### Security & defensive system (your PC's shield)
Defensive only — PHANTRON never attacks or scans anyone else, just protects
P7's own machine.
- **"security audit"** — a health report with a score out of 100 (Defender,
  firewall, Windows Update, open ports, suspicious processes).
- **"scan my pc"** / **"full scan"** — triggers Microsoft Defender.
- **"defender status"**, **"firewall status / on / off"**.
- **"open ports"**, **"active connections"** — see what's exposed/talking out.
- **"scan suspicious"** — heuristic check for hidden/encoded commands and
  executables running from risky folders (Temp, etc.).
- **"quarantine \<file\>"** — move a flagged file into a locked quarantine folder
  (reversible).
- **"harden"** — a safety checklist.

### Messaging & email
- **WhatsApp** — "whatsapp +9198... saying I'll be late" (instant with
  `pywhatkit`, else opens WhatsApp Web pre-filled).
- **Telegram** — set a bot token + chat_id in `config.json` under
  `messaging.telegram`, then "telegram remind the team".
- **Email** — set `messaging.email` (use a Gmail **App Password**). Then
  "read my email" or send via the `send_email` tool.

### Self-healing (resilience)
- Every command runs inside a crash-net: one broken skill never takes the
  whole assistant down. Errors are logged to `~/Phantron/selfheal.log`.
- **"explain the last error"** — plain-language explanation (uses the brain).
- **"fix file path/to/x.py"** — PHANTRON reads the file, asks the brain for a
  correction, **verifies it compiles**, backs up the original to `.bak`, then
  writes the fix. If the fix doesn't compile, nothing is changed.
- **"fix this error"** — PHANTRON looks at your screen (OCR), finds the error
  message, and explains the likely fix (vision + brain combo).

### Scheduling, files, backup & voice-lock
- **Daily reminders** — "remind me to drink water every day at 9:00" or
  "every day at 18:30 remind me gym". Survives restarts; "list schedules" to
  view, and remove by number.
- **File search** — "where is my resume", "find budget.xlsx" — scans your
  Desktop/Documents/Downloads/etc. and lists matches.
- **Auto-backup** — "backup my documents" zips a folder into
  `~/Phantron/backups`.
- **Voice passphrase** — "set my voice passphrase to phantom unlock" gates
  sensitive commands behind a spoken/typed phrase (stored locally).

### Vision (PHANTRON can SEE the screen)
- `read_screen` — OCR the whole screen to text.
- `see_screen` — ask a vision-capable model what's on screen (or fall back to OCR).
- `click_text` — find on-screen text and click it.

Needs `mss` + `pytesseract` (and the Tesseract engine installed). For
`see_screen` with a vision model, set `brain.vision_model` (e.g. `gpt-4o-mini`,
`llava`, `claude-3-5-sonnet-latest`).

### Memory (PHANTRON remembers across sessions)
Facts you tell it ("remember that my gpu is RTX 4060") persist to
`~/Phantron/memory/facts.json`, and a rolling conversation journal keeps
continuity between runs. Recall with "what's my gpu" or the `recall` tool.

### Voice (advanced)
- Auto-picks a Hindi/Indian-English voice when available; tune `voice.rate`,
  `voice.volume`, `voice.tts_voice`, `voice.engine`.
- Offline speech-to-text via **Vosk** (set `voice.vosk_model` to a model
  folder) — no internet needed; otherwise free Google web STT is used.

---

## Architecture

```
phantron/
  __main__.py    entry point (web UI / --cli / --voice / --voices / --status)
  config.py      config.json loading + sane defaults
  brain.py       multi-provider LLM client + vision (stdlib only)
  skills.py      the concrete actions + safety guard
  agent.py       decision loop (think -> act -> speak), memory-aware
  memory.py      persistent facts + journal + reminders + export
  vision.py      screen capture, OCR, find/click text, describe
  voice.py       advanced speech in/out (Vosk offline STT + smart TTS)
  security.py    defensive shield: audit, AV, firewall, ports, heuristics
  messaging.py   WhatsApp / Telegram / email send + read
  selfheal.py    crash-net + auto-fix Python files (verified, reversible)
  assistant.py   orchestrator wiring it all together
  server.py      stdlib web server + JSON API
  ui/index.html  the cyberpunk interface
```

## Safety
A guard blocks irreversible shell commands (disk formats, recursive root
deletes, fork bombs, …) and blocks shutdown/restart unless you enable
`agent.allow_shutdown` in `config.json`. Tune via `agent.safe_mode`.

---
Built for P7. 🛰️
