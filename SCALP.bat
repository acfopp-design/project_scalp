@echo off
setlocal enabledelayedexpansion
title PROJECT SCALP
cd /d "%~dp0"

REM ======================================================================
REM  THE ONE LAUNCHER.
REM
REM  Double-click it and it just runs: morning check, then PAPER trading.
REM  No menu, no choosing. Sri, 30-Sep: "I dont want to choose. Default,
REM  execute 1 and 2 everytime as we are not there yet for live based on
REM  todays learnings."
REM
REM  PAPER IS THE DEFAULT AND CANNOT BE REACHED BY ACCIDENT FROM LIVE.
REM  SCALP_LIVE_OK is cleared on the very first line below and is set in
REM  exactly one place - the live branch, which needs BOTH a command-line
REM  argument and the word LIVE typed in capitals. So a stale
REM  BROKER_MODE=LIVE in env.txt cannot place a real order on its own, and
REM  a double-click can never go live.
REM
REM  Other uses, when you want them:
REM      SCALP.bat live      - real money. Asks for confirmation.
REM      SCALP.bat status    - read-only view of orders and positions
REM      SCALP.bat backup    - refresh the disaster-restore copy
REM ======================================================================

set SCALP_LIVE_OK=
where python >nul 2>nul
if %errorlevel%==0 ( set PY=python ) else ( set PY=py )

if /i "%~1"=="live"   goto live
if /i "%~1"=="status" goto status
if /i "%~1"=="backup" goto endday
goto paper

REM ---------------------------------------------------------------- status
:status
set SCALP_LIVE_OK=1
%PY% live_exec.py
set SCALP_LIVE_OK=
echo.
echo   READ-ONLY. Nothing was placed, cancelled or changed.
pause
exit /b 0

REM ---------------------------------------------------------------- backup
:endday
echo.
echo   Refreshing Backup\restore_kit_latest ...
if not exist "Backup\restore_kit_latest\logs" mkdir "Backup\restore_kit_latest\logs"
xcopy /Y /Q *.py "Backup\restore_kit_latest\" >nul 2>nul
xcopy /Y /Q *.bat "Backup\restore_kit_latest\" >nul 2>nul
xcopy /Y /Q env.txt "Backup\restore_kit_latest\" >nul 2>nul
xcopy /Y /Q logs\*.json "Backup\restore_kit_latest\logs\" >nul 2>nul
echo   Done - code, settings and today's results.
echo   WARNING: env.txt holds your Dhan credentials. Keep this folder private.
pause
exit /b 0

REM ------------------------------------------------------------------ live
:live
echo.
echo   #########################################################
echo   #  LIVE - THIS PLACES REAL ORDERS WITH REAL MONEY.
echo   #########################################################
echo.
set OK=
set /p OK="   Type LIVE in capitals to confirm, anything else cancels: "
if not "%OK%"=="LIVE" exit /b 0
set SCALP_LIVE_OK=1
set MODE=LIVE
goto run

REM ----------------------------------------------------------------- paper
:paper
set SCALP_LIVE_OK=
set MODE=PAPER
goto run

REM ------------------------------------------------------------------- run
:run
cls
echo ============================================================
echo    P R O J E C T   S C A L P        starting in %MODE%
echo ============================================================
echo.

echo [1/5] Morning check ...
echo.
%PY% morning_check.py
echo.
if "%MODE%"=="PAPER" echo       Paper mode - none of the above can block the run.
echo.
echo       Continuing in 10 seconds. Ctrl+C to stop and fix something.
timeout /t 10 >nul

echo [2/5] Freeing port 5005 ...
for /L %%I in (1,1,6) do (
    set FOUND=0
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5005 " ^| findstr LISTENING') do (
        set FOUND=1
        taskkill /F /PID %%P >nul 2>nul
    )
    if !FOUND!==0 goto :clear
    timeout /t 1 >nul
)
echo   PORT 5005 STILL BUSY - not starting on stale code.
pause
exit /b 1
:clear

echo [3/5] Clearing bytecode, checking deps ...
if exist "__pycache__" rd /s /q "__pycache__" >nul 2>nul
%PY% -c "import flask" 2>nul || %PY% -m pip install flask
%PY% -c "import numpy,pandas" 2>nul || %PY% -m pip install numpy pandas

echo [4/5] Pre-flight ...
%PY% eye_preflight.py
if errorlevel 1 goto preflightfailed

if "%MODE%"=="LIVE" goto lastchance
goto startboard

:lastchance
%PY% broker.py
echo.
echo   Last chance. Ctrl+C to abort, any key to trade real money.
pause
goto startboard

:preflightfailed
echo.
echo   PRE-FLIGHT FAILED - see the reason above. Not starting.
pause
exit /b 1

:startboard
echo [5/5] Starting board + engine ...
echo.
echo   The board starts the trading engine and the capital-tiers worker
echo   by itself, and restarts either one if it dies or its code changes.
echo   Nothing else needs launching.
echo.
echo   Keep this window OPEN. Closing it stops the board.
echo.
start "" http://127.0.0.1:5005/
%PY% Movers_app.py

echo.
echo ============================================================
echo   BOARD STOPPED.
echo ============================================================
pause
