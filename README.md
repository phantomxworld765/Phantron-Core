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
run). Pick **one**:

### Option A — Local & free (recommended): Ollama
1. Install Ollama: https://ollama.com
2. Pull a model: `ollama pull llama3.1`  (or `qwen2.5`, `mistral`, …)
3. In `config.json` set `"brain": { "provider": "ollama" }` (or leave `"auto"`).

Private, offline, no limits, no cost.

### Option B — A cloud API key (OpenAI-compatible)
Set these under `brain.openai` in `config.json` (works with OpenAI, **Groq
(free tier)**, OpenRouter, LM Studio, Together, …):

```json
"openai": {
  "base_url": "https://api.groq.com/openai/v1",
  "api_key": "YOUR_KEY",
  "model": "llama-3.3-70b-versatile"
}
```

You can also pass the key via environment variable `OPENAI_API_KEY` /
`GROQ_API_KEY` instead of putting it in the file.

### Option C — Claude
Fill `brain.anthropic.api_key` (or env `ANTHROPIC_API_KEY`).

If no brain is configured, PHANTRON still runs in **command mode** (open apps,
play songs, system info, etc.) via a built-in rule parser.

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
`run_command`, `tell_time`.

In **autonomous** mode the brain decides which tools to call and can chain a
few of them to finish a request, then replies to you.

---

## Architecture

```
phantron/
  __main__.py    entry point (web UI / --cli / --voice)
  config.py      config.json loading + sane defaults
  brain.py       multi-provider LLM client (stdlib only)
  skills.py      the concrete actions + safety guard
  agent.py       decision loop (think -> act -> speak)
  voice.py       optional speech in/out
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
