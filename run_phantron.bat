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
    echo [ERROR] Python nahi mila. Python 3.10+ install karo aur PATH me add karo.
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

REM --- Install / update dependencies ---
echo [SETUP] Dependencies check kar raha hoon...
python -m pip install --upgrade pip >nul 2>nul
pip install -r requirements.txt

REM --- Launch PHANTRON ---
echo.
echo [PHANTRON] Start ho raha hai... Interface browser me khulega.
echo [PHANTRON] Yahin terminal me text command type kar sakte ho.
echo.
python phantron_core.py

echo.
echo [PHANTRON] Band ho gaya.
pause
