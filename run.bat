@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake
chcp 65001 >nul 2>&1
color 0B

REM Optional: run.bat 1  → dashboard + SMS lane 1
REM           run.bat 2  → dashboard + SMS lane 2
set "CHOICE=%~1"

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
echo      a u t o - s e t u p   ·   l a n e   p i c k e r
echo.
echo   ============================================================
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo.
  echo   [x] Setup failed. Check internet, then re-run run.bat
  echo       [it auto-installs Python + deps when missing].
  echo.
  pause
  exit /b 1
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

if not "%CHOICE%"=="" goto :dispatch

echo   What do you want to run?
echo.
echo      [0]  Dashboard only
echo      [1]  Dashboard + SMS create lane 1   [Warming SMS 1 · stop at 300]
echo      [2]  Dashboard + SMS create lane 2   [Warming SMS 2 · stop at 300]
echo      [3]  Dashboard + SMS create lane 3   [Warming SMS 3 · stop at 300]
echo      [4]  Dashboard + SMS create lane 4   [Warming SMS 4 · stop at 300]
echo      [Q]  Quit
echo.
set /p "CHOICE=   Pick 0-4 or Q: "

:dispatch
if /i "%CHOICE%"=="Q" exit /b 0
if /i "%CHOICE%"=="q" exit /b 0
if "%CHOICE%"=="0" goto :dash_only
if "%CHOICE%"=="1" goto :lane
if "%CHOICE%"=="2" goto :lane
if "%CHOICE%"=="3" goto :lane
if "%CHOICE%"=="4" goto :lane

echo   [x] Invalid choice: %CHOICE%
pause
exit /b 1

:dash_only
echo.
echo   AdsPower  : open + Local API :50325
echo   Dashboard : http://127.0.0.1:8787
echo.
echo   Ctrl+C stops the server.
echo   ------------------------------------------------------------
echo.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8787/"
"%VENVPY%" -m app
echo.
echo   Server stopped.
pause
exit /b 0

:lane
echo.
echo   Starting dashboard in a second window...
echo   This window will run SMS create lane %CHOICE%
echo   Stops when Warming SMS %CHOICE% has 300 good profiles.
echo   ------------------------------------------------------------
echo.

REM Start dashboard minimized in another CMD; wait until API answers.
start "SnappyMake Dashboard" /min cmd /c "cd /d "%~dp0" && "%VENVPY%" -m app"

echo   Waiting for dashboard on http://127.0.0.1:8787 ...
set "READY=0"
for /l %%i in (1,1,60) do (
  if "!READY!"=="0" (
    powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/api/runs/current -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 set "READY=1"
  )
  if "!READY!"=="0" timeout /t 2 /nobreak >nul
)
if not "!READY!"=="1" (
  echo   [x] Dashboard did not start. Open AdsPower, then re-run.
  pause
  exit /b 1
)

echo   [ok] Dashboard up. Starting lane %CHOICE% ...
echo.
"%VENVPY%" scripts\sms_create_lane.py --vps %CHOICE% --target 300
set "RC=%ERRORLEVEL%"

echo.
echo   ------------------------------------------------------------
if "%RC%"=="0" (
  echo   Lane %CHOICE% finished OK.
) else (
  echo   Lane %CHOICE% stopped with code %RC%.
)
echo   Log: data\logs\sms_create_vps%CHOICE%.log
echo   Dashboard window may still be running in the background.
echo.
pause
exit /b %RC%
