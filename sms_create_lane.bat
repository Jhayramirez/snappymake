@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake · SMS create lane
chcp 65001 >nul 2>&1
color 0A

if "%~1"=="" (
  cls
  echo.
  echo   ============================================================
  echo      S M S   C R E A T E   L A N E
  echo   ============================================================
  echo.
  echo   Usage:
  echo      sms_create_lane.bat 1
  echo      sms_create_lane.bat 2
  echo      sms_create_lane.bat 3
  echo      sms_create_lane.bat 4
  echo.
  echo   Map:
  echo      VPS 1 + 2  -^>  SnappyOfficial - Warming SMS 1
  echo      VPS 3 + 4  -^>  SnappyOfficial - Warming SMS 2
  echo.
  echo   Resume-safe: counts good profiles in AdsPower, then continues.
  echo   Logs: data\logs\sms_create_vpsN.log
  echo.
  echo   Start run.bat first (dashboard on :8787). AdsPower must be open.
  echo.
  pause
  exit /b 1
)

cls
echo.
echo   ============================================================
echo.
echo      S M S   C R E A T E   L A N E
echo      V P S   %~1
echo.
echo   ============================================================
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo   [x] Setup failed.
  pause
  exit /b 1
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

if "%~1"=="1" set "LANE=Warming SMS 1"
if "%~1"=="2" set "LANE=Warming SMS 1"
if "%~1"=="3" set "LANE=Warming SMS 2"
if "%~1"=="4" set "LANE=Warming SMS 2"

echo   Target group : SnappyOfficial - %LANE%
echo   Prefix       : V%~1-SMS
echo   Resume       : yes (AdsPower count)
echo   Cache clear  : yes after each batch (cookies kept)
echo   Log file     : data\logs\sms_create_vps%~1.log
echo.
echo   Need run.bat open. Ctrl+C stops this creator only.
echo   ------------------------------------------------------------
echo.

"%VENVPY%" scripts\sms_create_lane.py --vps %~1 %2 %3 %4 %5 %6
set "RC=%ERRORLEVEL%"

echo.
echo   ------------------------------------------------------------
if "%RC%"=="0" (
  echo   Lane finished OK.
) else (
  echo   Lane stopped with code %RC%.
)
echo   Log: data\logs\sms_create_vps%~1.log
echo.
pause
endlocal & exit /b %RC%
