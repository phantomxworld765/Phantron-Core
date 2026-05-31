@echo off
title PHANTRON v7 - P7 BEAST MODE
cd /d "%~dp0"
color 0B

echo ====================================================
echo   PHANTRON v7  -  Booting up...
echo ====================================================

REM --- Check Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python nahi mila. Python 3.10+ install karo aur "Add to PATH" tick karo.
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

REM --- Create virtual env on first run ---
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Virtual environment bana raha hoon...
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"

python -m pip install --upgrade pip >nul 2>nul

REM --- CORE dependencies (REQUIRED). If these fail, PHANTRON cannot run. ---
echo.
echo [SETUP] Core dependencies install kar raha hoon (zaroori)...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Core dependencies install nahi hui. Internet check karo aur dobara chalao.
    pause
    exit /b 1
)

REM --- VOICE dependencies (OPTIONAL). Failure here is OK - text still works. ---
echo.
echo [SETUP] Voice dependencies install kar raha hoon (optional - fail ho to bhi chalega)...
pip install -r requirements-voice.txt
if errorlevel 1 (
    echo [WARN]  Kuch voice packages install nahi hue ^(shayad pyaudio^). Koi baat nahi -
    echo         PHANTRON text aur interface se poori tarah chalega. Mic baad me laga sakte ho.
)

REM --- VISION dependencies (OPTIONAL). For OCR/click-by-text/autonomous eyes. ---
echo.
echo [SETUP] Vision dependencies install kar raha hoon (optional - autonomous "eyes")...
pip install -r requirements-vision.txt
if errorlevel 1 (
    echo [WARN]  Vision packages install nahi hue. Screen capture phir bhi chalega;
    echo         OCR/click-by-text ke liye baad me requirements-vision.txt install karna.
) else (
    echo [INFO]  OCR ke liye Tesseract-OCR engine bhi chahiye:
    echo         https://github.com/UB-Mannheim/tesseract/wiki  ^(PATH me add karo^)
)

REM --- Tell the user about the AI brain status ---
echo.
echo [INFO] AI BRAIN: PHANTRON ko sochne ke liye ek backend chahiye.
echo        - FREE local : Ollama install karo ^(https://ollama.com^), phir: ollama pull llama3
echo        - YA cloud   : config.json me "claude_api_key" daal do.
echo        Bina iske bhi: app kholna, gaana, screenshot, system info, time/date,
echo        aur basic baat-cheet kaam karegi ^(offline mode^).
echo.

REM --- Launch PHANTRON ---
echo [PHANTRON] Start ho raha hai... Interface browser me khulega.
echo [PHANTRON] Yahin terminal me bhi command type kar sakte ho.
echo.
python phantron_core.py

echo.
echo [PHANTRON] Band ho gaya.
pause
