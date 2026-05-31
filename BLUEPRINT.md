# PHANTRON v7 — BLUEPRINT (banane se PEHLE ka plan)

> Maqsad: build karne se pehle decide karna — **kya banega, kya nahi, kis order me**.
> Golden rule (jo galti hui thi uska fix): **pehle ek chhota CHALTA-HUA version,
> phir advance features**. Ek saath sab banakar "kuch kaam nahi karta" nahi karenge.

---

## 1. Vision (ek line)
P7 ka personal Jarvis-style AI assistant jo Windows PC par voice/text se chale,
PC control kare (apps, mouse, keyboard, screen), aur ek cyberpunk HUD me dikhe.

---

## 2. FEATURE DECISIONS — kya add, kya nahi

### ✅ ABHI (Phase 1 — MVP, ye 100% chalna chahiye)
| Feature | Kyun |
|---|---|
| Cyberpunk HUD interface | Ho chuka (Interface.html) |
| Text command (chat box) | Sabse basic, pakka chale |
| AI brain: OpenRouter / Ollama / offline fallback | Dimaag |
| App/website kholna (chrome, youtube, etc.) | Roz ka kaam |
| Web search | Roz ka kaam |
| Screenshot + System info (CPU/RAM/battery) | Live gauges |
| Volume / media keys | Asaan + useful |
| **Doctor (diagnose.bat)** | Kya toota hai turant pata chale |
| On/Off/Pause (safety) | Ho chuka |

### ⏳ BAAD ME (Phase 2 — jab MVP chal jaye)
| Feature | Note |
|---|---|
| Voice command + wake word | Mic deps chahiye (pyaudio) |
| Vision: screen padhna (OCR) + click-by-text | pytesseract + Tesseract engine |
| Autonomous task loop (khud multi-step) | AI brain strong hona chahiye |
| WhatsApp / YouTube skills | UI automation, testing chahiye |
| Code Doctor (bug fix / refactor) | Already coded, test pending |

### ⏳ PHASE 3 (advance / heavy)
| Feature | Note |
|---|---|
| Deep automation: Blender / Android / ffmpeg | App-specific, ek-ek karke |
| Model switch command ("opus use karo") | Chhota, useful |
| 24/7 background daemon + tray icon + autostart | pythonw + Windows startup |

### ❌ NAHI BANEGA (honest — possible/safe nahi)
| Cheez | Kyun nahi |
|---|---|
| Lock screen unlock/bypass | Windows security wall — koi app nahi kar sakti |
| Free me Opus 4.8 (unlimited) | Premium paid model, koi legit free tarika nahi |
| "Khud naya AI model train karna" | Data + GPU + hafte chahiye; ye ek button ka kaam nahi |
| Cracked/shared API keys | Illegal + account ban + risky |

---

## 3. BUILD PHASES (order)

```
PHASE 1  →  MVP jo CHALE        →  [test on PC]  →  user OK?
PHASE 2  →  Vision + Voice + Skills
PHASE 3  →  Deep automation + 24/7 daemon
```
Har phase ke baad: **tum PC par test karoge**, chalega tabhi agle phase par.

---

## 4. INTERFACE LAYOUT (wireframe)

```
+-------------------------------------------------------------------+
|  (orb) PHANTRON v7        [ONLINE][AI][OS][clock]   [⏸][⏻]         |  header
+----------------+------------------------------+-------------------+
| SYSTEM         |   OPERATING CORE / HUD        | CYBERSECURITY     |
| DIAGNOSTICS    |   ( glowing HEART CORE )      | - Firewall bars   |
| - CPU ring     |   System Health bar          | - Matrix grid     |
| - RAM ring     |   ---- CONVERSATION ----      | - Threat level    |
| - Battery ring |   [chat messages...]          |-------------------|
| - Network bars |                              | SECURE TUNNELS /  |
| - OS latency   |   VOICE COGNITION ~~~~~~      | Activity log      |
| - Visual feed  |   [ type... ][mic][SEND]      |                   |
+----------------+------------------------------+-------------------+
|  NOTIFICATIONS        |  CONTROL DOCK: [SysInfo][Screenshot]...    |  dock
+-------------------------------------------------------------------+
```
> Live mockup: `Interface.html` ko browser me kholo — yahi dikhega.

---

## 5. ARCHITECTURE (module map)

```
run_phantron.bat ──> phantron_core.py  (server + brain + parser + voice)
                         │
   ┌─────────┬───────────┼───────────┬──────────┬───────────┐
 vision   autonomous  codedoctor   apps      skills      macros
 (eyes)   (loop)      (self-fix)  (launch)  (wa/yt)    (blender/android)
                         │
                    Interface.html (HUD, WebSocket ws://localhost:8765)
 config.json = settings (AI key, model, apps)   |  diagnose.bat = health check
```

---

## 6. SETUP (jo chahiye hoga)
- Python 3.10+ (PATH me)
- `pip install -r requirements.txt` (core)
- AI: OpenRouter key (ya `:free` model) **ya** Ollama (free local)
- Optional: voice deps, vision deps (Tesseract)

---

## 7. MUJHE TUMSE CHAHIYE (banane se pehle decide karo)
1. Phase 1 (MVP) ki feature-list theek hai? Kuch add/hatana?
2. Interface ka look (HUD) final hai, ya colors/layout badalna hai?
3. AI brain default: OpenRouter free model, ya Ollama (free local), ya paid Opus 4.8?
4. Haan milte hi main Phase 1 ko rock-solid karke, tumhare PC par test karwaunga.
```
```
