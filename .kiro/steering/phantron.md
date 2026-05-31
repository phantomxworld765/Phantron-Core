---
inclusion: always
---

# PHANTRON-Core — Project Context (read me first)

PHANTRON is **P7's personal, Jarvis-style autonomous AI assistant** that runs
locally on the user's own Windows PC. It listens (voice + text + a web UI),
understands Hindi/Hinglish commands, and controls the machine: opens apps, plays
media, controls mouse/keyboard, reads the screen (vision/OCR), runs autonomous
multi-step tasks, and even refactors/auto-fixes its own code.

> Communicate with the user (P7) in **Hindi/Hinglish**, friendly and simple.
> PHANTRON's own replies to the user are in Hindi and address the user as "P7".

## Architecture (all modules are import-safe & degrade gracefully)

| File | Role |
|---|---|
| `phantron_core.py` | Main server: WebSocket on `ws://localhost:8765`, serves `Interface.html`, AI brain (tool-calling JSON), local command parser, voice I/O |
| `phantron_vision.py` | Computer-vision "eyes": screen OCR, find/click text, template match, describe, change-detect |
| `phantron_autonomous.py` | Continuous perceive→think→act loop; `is_destructive()` safety guard |
| `phantron_codedoctor.py` | Static analysis + AI refactor/auto-fix (backup + compile-check) + self-heal |
| `phantron_apps.py` | App registry + smart launcher (url/uri/exe/path), config-overridable |
| `phantron_skills.py` | WhatsApp send/read, YouTube search/play/skip-ad/next/pause, keep-awake; window focus via `_ensure_app` |
| `phantron_macros.py` | DEEP automation (script/CLI): Blender bpy, Android gradle, ffmpeg, python |
| `phantron_os_controller.py` | Agentic OS task runner (heavy builds) |
| `phantron_security_audit.py` | Defensive security scan |
| `Interface.html` | Cyberpunk/Jarvis web UI (boot sequence, gauges, live screen, chat, mic) |
| `config.json` | All settings (AI mode/keys, apps, voice, agent limits) |

## Conventions (follow these when editing)

- **Never crash on a missing optional library.** Return a clear Hindi message
  telling the user what to `pip install`. Core deps only in `requirements.txt`;
  voice in `requirements-voice.txt`; vision in `requirements-vision.txt`.
- **Adding a tool:** write a `do_<name>(...)` executor, register it in
  `_ACTION_DISPATCH`, document it in the `ACTION_SYSTEM` prompt string, and add a
  `parse_and_execute` trigger. Explicit/prefix commands go at the **TOP** of the
  parser so their arguments aren't hijacked by generic open/play matches.
- **Safety first:** destructive shell commands are blocked in `agent_safe_mode`.
  Never bypass the OS lock screen (impossible + unsafe) — use `keep_awake` instead.
- **Manual control:** `_control_intent()` detects pause/resume/shutdown from voice/
  text; the server gates all commands on a global `PAUSED` flag. Interface has ⏸/⏻
  buttons (send `{type:"control",action:...}`). `stop_phantron.bat` kills the process
  via the `phantron.pid` file written on startup. Keep media commands like
  "music band karo" OUT of the shutdown matcher.
- **AI access** is via `_ai_raw(prompt, system)`; it auto-selects the configured
  online provider (OpenRouter `sk-or-...` or Claude `sk-ant-...`) → Ollama (local)
  → built-in offline brain. Backends: `_ask_openrouter_raw`, `_ask_claude_raw`,
  `_ask_ollama_raw`. OpenRouter is OpenAI-compatible (`/api/v1/chat/completions`).
- New modules should be **decoupled** (dependency-injected) and have a `__main__`
  smoke test, like the existing ones.

## Build / test notes

- All Python files must `python -m py_compile` cleanly.
- The dev sandbox has **no internet and no pip**; test logic by stubbing the
  `websockets` module so `phantron_core` imports, then call functions directly.
