@echo off
setlocal EnableExtensions EnableDelayedExpansion
title SnappyMake launcher

REM ------------------------------------------------------------------ #
REM  SnappyMake auto-launcher for Windows
REM  - finds Python, creates .venv if missing
REM  - installs / updates requirements
REM  - starts the IMAP companion (new window) + the dashboard
REM ------------------------------------------------------------------ #

REM Always run from the folder this script lives in.
cd /d "%~dp0"

echo(
echo === SnappyMake launcher ===
echo Folder: %cd%
echo(

REM ---- 1. Locate a Python launcher --------------------------------- #
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] Python 3 was not found on PATH.
    echo         Install Python 3.11+ from https://www.python.org/downloads/windows/
    echo         and tick "Add python.exe to PATH" during setup.
    echo(
    pause
    exit /b 1
)
echo [ok] Using Python launcher: %PYCMD%

REM ---- 2. Create the virtual environment if needed ----------------- #
set "VENV_PY=%cd%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [..] Creating virtual environment in .venv
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [ok] Virtual environment already present.
)

REM ---- 3. Install / update dependencies ---------------------------- #
echo [..] Ensuring pip is up to date
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1

echo [..] Installing requirements (this is quick when already satisfied)
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. See the messages above.
    pause
    exit /b 1
)
echo [ok] Dependencies ready.

REM ---- 4. Start the IMAP companion in its own window --------------- #
echo [..] Starting IMAP companion on http://127.0.0.1:8799
start "SnappyMake Companion" cmd /k ""%VENV_PY%" "%cd%\extension\companion\companion.py""

REM ---- 5. Open the dashboard in the browser ------------------------ #
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8787

REM ---- 6. Run the dashboard (keeps this window open) --------------- #
echo(
echo === Dashboard running on http://127.0.0.1:8787 ===
echo Close this window (or press Ctrl+C) to stop the dashboard.
echo The companion runs in the separate "SnappyMake Companion" window.
echo(
"%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8787

echo(
echo Dashboard stopped.
pause
endlocal
