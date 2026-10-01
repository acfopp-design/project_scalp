@echo off
REM ====================================================================
REM  LOGPUSH.bat - auto-pushes the logs/ folder to GitHub every 90s.
REM  Double-click this ONCE in the morning (separate window from SCALP).
REM  It pushes ONLY logs/ - never your code. Close the window to stop.
REM ====================================================================
cd /d "%~dp0"
title LOGPUSH - auto-sync logs
:loop
git add logs/ 1>nul 2>nul
git commit -m "logs auto-sync %date% %time%" 1>nul 2>nul
git push 1>nul 2>nul
echo [%time%] pushed logs
timeout /t 90 >nul
goto loop
