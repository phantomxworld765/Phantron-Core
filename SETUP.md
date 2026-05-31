# PHANTRON v7 — Setup Guide (P7)

Ye guide step-by-step batati hai ki PHANTRON ko **full power** me kaise chalaayein.
Sab Hindi/Hinglish me, simple steps me.

> **Sabse fast start:** `run_phantron.bat` double-click karo. Wo core deps install
> karke interface khol dega. Bina AI ke bhi basic commands (app kholna, gaana,
> screenshot, time/date) chal jaayenge. Neeche steps se ise smart + advance banao.

---

## 1. Python install (ek baar)

1. https://www.python.org/downloads/ se Python 3.10+ install karo.
2. Installer me **"Add Python to PATH"** zaroor tick karo.
3. Check: terminal (cmd) me `python --version` likho — version dikhna chahiye.

---

## 2. PHANTRON start karo

Project folder me `run_phantron.bat` double-click karo. Pehli baar ye:
- ek virtual environment banata hai (`.venv`),
- **core** dependencies install karta hai (zaroori),
- **voice** + **vision** dependencies best-effort install karta hai (fail ho to bhi chalega),
- interface browser me kholta hai + terminal me command lene lagta hai.

Terminal me ek **CAPABILITY CHECK** dikhega — wo batata hai kya ON hai, kya missing.

---

## 3. AI Brain — 2 options (koi ek chuno)

PHANTRON ko "sochne" (general baat, planning, code/refactor, autonomous tasks)
ke liye ek AI backend chahiye. Bina iske sirf hardcoded commands chalte hain.

### Option A — Ollama (FREE, local, offline chalta hai)

1. https://ollama.com se Ollama install karo.
2. Terminal me model download karo:
   ```
   ollama pull llama3
   ```
   (chaaho to behtar: `ollama pull llama3.1` ya `qwen2.5`)
3. Ollama background me chalu rakho:
   ```
   ollama serve
   ```
4. `config.json` me set karo:
   ```json
   "ai_mode": "ollama",
   "ollama_model": "llama3"
   ```
5. Bas! PHANTRON ka AI ab ONLINE.

### Option B — Claude / Opus 4.8 (cloud, paid, sabse smart)

1. https://console.anthropic.com par account banao → **API Keys** → nayi key banao
   (key aisi dikhti hai: `sk-ant-...`). Isme usage ke hisaab se paisa lagta hai.
2. `config.json` me apni key paste karo:
   ```json
   "ai_mode": "claude",
   "claude_api_key": "sk-ant-yahan-apni-key",
   "claude_model": "claude-opus-4-8"
   ```
3. `pip install anthropic` (voice/vision install ke saath aa jaata hai, warna manually).
4. Done — ab PHANTRON Opus 4.8 par chalega.

> **Security:** Apni API key kisi ke saath share mat karna aur GitHub par push
> mat karna. `config.json` ko private rakho. (Key kabhi code me hardcode na karein —
> hamesha config.json me hi rahe.)

> **Fallback:** Agar Claude key na ho, PHANTRON khud Ollama try karega; wo bhi na ho
> to **offline brain** (greetings, time, date, hisaab, app/gaana/screenshot) chalega.

---

## 4. Optional dependencies (jo feature chahiye wahi)

`.venv` activate karke (`.venv\Scripts\activate`) install karo:

| Feature | Command |
|---|---|
| Mic + bola hua jawab (voice) | `pip install -r requirements-voice.txt` |
| Screen padhna / click-by-text / ad-skip (OCR) | `pip install -r requirements-vision.txt` |
| OCR engine (vision ke liye zaroori) | [Tesseract install](https://github.com/UB-Mannheim/tesseract/wiki) + PATH me add |
| Heavy automation (jo app use karo) | Blender / Android Studio / ffmpeg alag se install |

> `pyaudio` (mic) Windows par build fail kare to: `pip install pipwin` phir `pipwin install pyaudio`.

---

## 5. Heavy app automation ke paths (optional)

`config.json` ke `apps` block me bade apps ka exact path daalo (jab tak PATH me na ho):

```json
"apps": {
  "unreal":  {"type": "path", "target": "D:\\UE_5.4\\Engine\\Binaries\\Win64\\UnrealEditor.exe"},
  "blender": {"type": "path", "target": "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe"},
  "android studio": {"type": "path", "target": "C:\\Program Files\\Android\\Android Studio\\bin\\studio64.exe"}
}
```

---

## 6. Bolne/likhne ke liye example commands

```
chrome kholo
youtube pe lofi beats chalao
skip ad   |   agla video   |   video pause karo
whatsapp pe Rahul ko hello bhai bhejo
whatsapp Rahul ki chat padho
screen me kya hai          (vision)
submit par click karo      (click-by-text, vision)
auto: notepad kholo aur ek note likho   (autonomous)
analyze phantron_core.py   |   fix bug in mycode.py   |   refactor utils.py
blender me ek red cube banao aur render karo
android build D:\MyApp
teaser banao input.mp4 ka 15 second
python script banao ek calculator
```

---

## 7. 24/7 mode

`config.json` me `"keep_awake": true` karo — PHANTRON PC ko idle-sleep/lock hone se
rokega taaki wo hamesha command ke liye taiyaar rahe.

> **Note:** Ye sirf inactivity-lock rokta hai. Pehle se **LOCKED** screen ko unlock
> karna Windows security ki wajah se possible nahi hai (aur safe bhi nahi). Agar
> chaaho to Windows me apna auto-login khud set kar sakte ho.

---

## 8. Common problems

| Problem | Fix |
|---|---|
| "AI brain offline" | Section 3 follow karo (Ollama serve ya Claude key) |
| CPU/RAM gauges 0% | `pip install -r requirements.txt` (psutil) |
| Screenshot/mouse/screen kaam nahi | `pip install pyautogui Pillow` |
| Port 8765 busy | Pehle wala PHANTRON band karo, ya config me `ws_port` badlo |
| Interface "OFFLINE" | PHANTRON terminal me chal raha hai? Browser refresh karo |
| App nahi khulta | `config.json` ke `apps` me uska full path daalo |

---

Help chahiye to PHANTRON ko hi bolo: **"help"** — wo apni saari commands bata dega.
