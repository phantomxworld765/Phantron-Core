@echo off
REM ─────────────────────────────────────────────────────────────
REM  PHANTRON launcher for Windows
REM  Double-click this file to start PHANTRON.
REM ─────────────────────────────────────────────────────────────
title PHANTRON
cd /d "%~dp0"

REM Pick whichever Python launcher is available.
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYEXE=py -3"
) else (
  set "PYEXE=python"
)

echo.
echo   Starting PHANTRON...  (browser khulega apne aap)
echo.

REM Default: web UI. Add  --voice  for wake-word listening, or  --cli  for terminal.
%PYEXE% -m phantron %*

echo.
echo   PHANTRON band ho gaya. Window band karne ke liye koi bhi key dabao.
pause >nul
