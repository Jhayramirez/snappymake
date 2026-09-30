@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake · Dashboard
chcp 65001 >nul 2>&1
color 0B

cls
echo.
echo   ============================================================
echo.
echo      ____                       __  ____       __
echo     / __/__  ___ ____  ___ __  /  ^|/  /__ ____/ /_____
echo    _\ \/ _ \/ _ `/ _ \/ _ `/  / /^|_/ / _ `/ _  / -_)
echo   /___/_//_/\_,_/ .__/\_, /  /_/  /_/\_,_/\_,_/\__/
echo                /_/   /___/
echo.
echo      D A S H B O A R D   ·   a u t o - s e t u p
echo.
echo   ============================================================
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo.
  echo   [x] Setup failed. Check internet, then re-run run.bat
  echo       (it auto-installs Python + deps when missing).
  echo.
  pause
  exit /b 1
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

echo   AdsPower  : open + Local API :50325
echo   Dashboard : http://127.0.0.1:8787
echo   Gmail     : http://127.0.0.1:8787/gmail-login
echo.
echo   Ctrl+C stops the server.
echo   ------------------------------------------------------------
echo.

start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8787/gmail-login"

"%VENVPY%" -m app

echo.
echo   ------------------------------------------------------------
echo   Server stopped.
echo.
pause
endlocal
