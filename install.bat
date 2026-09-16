@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title SnappyMake - Windows Setup

echo ================================================
echo    SnappyMake  -  Windows one-click setup
echo ================================================
echo.

REM --- Locate Python (prefer the py launcher, fall back to python) ---
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python was not found on this PC.
  echo         Install Python 3.12+ from https://www.python.org/downloads/
  echo         IMPORTANT: tick "Add python.exe to PATH" during install, then re-run install.bat
  echo.
  pause
  exit /b 1
)

for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo Using %%v

echo.
echo [1/4] Creating virtual environment (.venv) ...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create the virtual environment.
    pause & exit /b 1
  )
) else (
  echo       .venv already exists - reusing it.
)

set "VENVPY=.venv\Scripts\python.exe"

echo.
echo [2/4] Upgrading pip ...
"%VENVPY%" -m pip install --upgrade pip

echo.
echo [3/4] Installing Python packages (this can take a few minutes) ...
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check your internet connection and re-run.
  pause & exit /b 1
)

echo.
echo [4/4] Installing Playwright Chromium ...
"%VENVPY%" -m playwright install chromium

echo.
echo ================================================
echo    Setup complete.
echo ================================================
echo.
echo Make sure AdsPower is OPEN with the Local API enabled (port 50325).
echo Starting SnappyMake now...
echo.
timeout /t 2 >nul
call "%~dp0run.bat"
endlocal
