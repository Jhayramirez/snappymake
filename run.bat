@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake
chcp 65001 >nul 2>&1
color 0B

REM Optional: run.bat 1        → menu for continue/fresh on lane 1
REM           run.bat 1 c      → continue lane 1
REM           run.bat 1 f      → fresh wipe + lane 1
set "CHOICE=%~1"
set "MODE=%~2"

cls
echo.
echo   +--------------------------------------------------------------+
echo   ^|                                                              ^|
echo   ^|   S N A P P Y M A K E                                        ^|
echo   ^|   creation lanes  ·  auto-setup  ·  watchable VPS            ^|
echo   ^|                                                              ^|
echo   +-------------------------------+------------------------------+
echo   ^|  LEFT · pick a lane           ^|  RIGHT · what it does        ^|
echo   +-------------------------------+------------------------------+
echo   ^|  [0]  Dashboard only          ^|  Server on :8787             ^|
echo   ^|  [1]  SMS create lane 1       ^|  Warming SMS 1  ·  stop 300  ^|
echo   ^|  [2]  SMS create lane 2       ^|  Warming SMS 2  ·  stop 300  ^|
echo   ^|  [3]  SMS create lane 3       ^|  Warming SMS 3  ·  stop 300  ^|
echo   ^|  [4]  SMS create lane 4       ^|  Warming SMS 4  ·  stop 300  ^|
echo   ^|  [Q]  Quit                    ^|  Exit                        ^|
echo   +-------------------------------+------------------------------+
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo.
  echo   [x] Setup failed. Check internet, then re-run run.bat
  echo.
  pause
  exit /b 1
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

if not "%CHOICE%"=="" goto :dispatch

set /p "CHOICE=   Pick 0-4 or Q: "

:dispatch
if /i "%CHOICE%"=="Q" exit /b 0
if /i "%CHOICE%"=="q" exit /b 0
if "%CHOICE%"=="0" goto :dash_only
if "%CHOICE%"=="1" goto :lane_mode
if "%CHOICE%"=="2" goto :lane_mode
if "%CHOICE%"=="3" goto :lane_mode
if "%CHOICE%"=="4" goto :lane_mode

echo   [x] Invalid choice: %CHOICE%
pause
exit /b 1

:dash_only
echo.
echo   +-------------------------------+------------------------------+
echo   ^|  STATUS                       ^|  LINKS                       ^|
echo   +-------------------------------+------------------------------+
echo   ^|  Dashboard starting           ^|  http://127.0.0.1:8787       ^|
echo   ^|  AdsPower Local API           ^|  :50325                      ^|
echo   ^|  Ctrl+C stops server          ^|  Gmail page opens shortly    ^|
echo   +-------------------------------+------------------------------+
echo.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8787/"
"%VENVPY%" -m app
echo.
echo   Server stopped.
pause
exit /b 0

:lane_mode
if not "%MODE%"=="" goto :mode_dispatch
echo.
echo   +-------------------------------+------------------------------+
echo   ^|  LANE %CHOICE%  MODE                     ^|  DETAIL                       ^|
echo   +-------------------------------+------------------------------+
echo   ^|  [C]  Continue                ^|  Keep profiles, resume count ^|
echo   ^|  [F]  Fresh                   ^|  WIPE Warming SMS %CHOICE%, start 0^|
echo   ^|  [B]  Back                    ^|  Return to lane grid         ^|
echo   +-------------------------------+------------------------------+
echo.
set /p "MODE=   Pick C / F / B: "

:mode_dispatch
if /i "%MODE%"=="B" (
  set "CHOICE="
  set "MODE="
  cls
  goto :eof
)
if /i "%MODE%"=="b" (
  set "CHOICE="
  set "MODE="
  cls
  echo Re-run run.bat to pick again.
  pause
  exit /b 0
)
if /i "%MODE%"=="C" set "FRESH_FLAG="
if /i "%MODE%"=="c" set "FRESH_FLAG="
if /i "%MODE%"=="F" set "FRESH_FLAG=--fresh"
if /i "%MODE%"=="f" set "FRESH_FLAG=--fresh"
if /i "%MODE%"=="C" goto :lane
if /i "%MODE%"=="c" goto :lane
if /i "%MODE%"=="F" goto :confirm_fresh
if /i "%MODE%"=="f" goto :confirm_fresh

echo   [x] Invalid mode: %MODE%
pause
exit /b 1

:confirm_fresh
echo.
echo   +-------------------------------+------------------------------+
echo   ^|  FRESH CONFIRM                ^|  TYPE YES                    ^|
echo   +-------------------------------+------------------------------+
echo   ^|  Deletes ALL in Warming SMS %CHOICE%^|  then create to 300 good    ^|
echo   +-------------------------------+------------------------------+
echo.
set /p "CONFIRM=   Confirm: "
if /i not "%CONFIRM%"=="YES" (
  echo   Cancelled.
  pause
  exit /b 1
)
set "FRESH_FLAG=--fresh"
goto :lane

:lane
echo.
if defined FRESH_FLAG (
  set "MODE_LABEL=FRESH wipe + create"
) else (
  set "MODE_LABEL=CONTINUE resume"
)
echo   +-------------------------------+------------------------------+
echo   ^|  STATUS                       ^|  LOG                         ^|
echo   +-------------------------------+------------------------------+
echo   ^|  Lane %CHOICE%  !MODE_LABEL!            ^|  data\logs\sms_create_vps%CHOICE%.log ^|
echo   ^|  Group Warming SMS %CHOICE%           ^|  live grid in this window  ^|
echo   ^|  Stop at 300 good             ^|  dashboard :8787 background  ^|
echo   +-------------------------------+------------------------------+
echo.

start "SnappyMake Dashboard" /min cmd /c "cd /d %~dp0 && .venv\Scripts\python.exe -m app"

echo   Waiting for dashboard ...
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

echo   [ok] Dashboard up.
echo.
"%VENVPY%" scripts\sms_create_lane.py --vps %CHOICE% --target 300 --ui %FRESH_FLAG%
set "RC=%ERRORLEVEL%"

echo.
echo   +-------------------------------+------------------------------+
if "%RC%"=="0" (
  echo   ^|  DONE OK                       ^|  Lane %CHOICE%                        ^|
) else (
  echo   ^|  STOPPED code %RC%                ^|  Lane %CHOICE%                        ^|
)
echo   ^|  Log file                     ^|  data\logs\sms_create_vps%CHOICE%.log ^|
echo   +-------------------------------+------------------------------+
echo.
pause
exit /b %RC%
