@echo off
setlocal
cd /d "%~dp0"
title SnappyMake SMS create lane

if "%~1"=="" (
  echo Usage: sms_create_lane.bat 1
  echo        sms_create_lane.bat 2
  echo        sms_create_lane.bat 3
  echo        sms_create_lane.bat 4
  echo.
  echo VPS 1 + 2 create into: SnappyOfficial - Warming SMS 1
  echo VPS 3 + 4 create into: SnappyOfficial - Warming SMS 2
  echo.
  echo Start run.bat first so the dashboard is on http://127.0.0.1:8787
  echo AdsPower must be open with Local API enabled.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Not set up yet. Run install.bat first.
  pause
  exit /b 1
)

echo ================================================
echo   SMS create lane  VPS %~1
echo ================================================
echo.
echo Ctrl+C stops this creator. Dashboard window stays open.
echo.

".venv\Scripts\python.exe" scripts\sms_create_lane.py --vps %~1 %2 %3 %4 %5 %6
echo.
echo Lane stopped.
pause
endlocal
