@echo off
setlocal enabledelayedexpansion
title Stop PHANTRON
cd /d "%~dp0"
color 0C

echo ====================================================
echo   PHANTRON ko band kar raha hoon...
echo ====================================================

REM --- Preferred: use the PID file written by phantron_core.py on startup ---
if exist phantron.pid (
    set /p PID=<phantron.pid
    taskkill /f /pid !PID! >nul 2>nul
    if not errorlevel 1 (
        echo [OK] PHANTRON ^(PID !PID!^) band ho gaya P7.
    ) else (
        echo [INFO] Us PID par PHANTRON nahi mila ^(shayad pehle se band tha^).
    )
    del phantron.pid >nul 2>nul
    goto done
)

REM --- Fallback: terminate the python process running phantron_core.py ---
echo [INFO] phantron.pid nahi mila. phantron_core wale python ko dhundh ke band kar raha hoon...
wmic process where "name='python.exe' and commandline like '%%phantron_core%%'" call terminate >nul 2>nul
wmic process where "name='pythonw.exe' and commandline like '%%phantron_core%%'" call terminate >nul 2>nul
echo [OK] Ho gaya P7 ^(agar chal raha tha to band kar diya^).

:done
timeout /t 2 >nul
endlocal
