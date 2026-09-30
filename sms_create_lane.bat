@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake SMS create lane
chcp 65001 >nul 2>&1
color 0A

if not "%~1"=="" goto :run

cls
echo.
echo   ============================================================
echo      S M S   C R E A T E   L A N E
echo   ============================================================
echo.
echo   Usage:
echo      .\sms_create_lane.bat 1
echo      .\sms_create_lane.bat 2
echo      .\sms_create_lane.bat 3
echo      .\sms_create_lane.bat 4
echo.
echo   Map [1 VPS = 1 group]:
echo      VPS 1  -^>  SnappyOfficial - Warming SMS 1
echo      VPS 2  -^>  SnappyOfficial - Warming SMS 2
echo      VPS 3  -^>  SnappyOfficial - Warming SMS 3
echo      VPS 4  -^>  SnappyOfficial - Warming SMS 4
echo.
echo   Default target: 300 good profiles per group.
echo   Resume-safe. Logs: data\logs\sms_create_vpsN.log
echo.
echo   Start run.bat first. AdsPower must be open.
echo   In PowerShell use:  .\sms_create_lane.bat 1
echo.
pause
exit /b 1

:run
cls
echo.
echo   ============================================================
echo      S M S   C R E A T E   L A N E
echo      V P S   %~1
echo   ============================================================
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo   [x] Setup failed.
  pause
  exit /b 1
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

echo   Target group : SnappyOfficial - Warming SMS %~1
echo   Prefix       : V%~1-SMS
echo   Target       : 300 good [default]
echo   Resume       : yes
echo   Cache clear  : yes after each batch [cookies kept]
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
