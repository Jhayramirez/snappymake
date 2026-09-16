@echo off
setlocal
cd /d "%~dp0"
title SnappyMake

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Not set up yet. Run install.bat first.
  echo.
  pause
  exit /b 1
)

echo ================================================
echo    SnappyMake dashboard
echo ================================================
echo.
echo Reminder: AdsPower must be OPEN with Local API enabled (port 50325).
echo Dashboard:   http://127.0.0.1:8787
echo Gmail Login: http://127.0.0.1:8787/gmail-login
echo.
echo Press Ctrl+C in this window to stop the server.
echo.

REM Open the Gmail Login page in the default browser after a short delay.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8787/gmail-login"

".venv\Scripts\python.exe" -m app

echo.
echo Server stopped.
pause
endlocal
