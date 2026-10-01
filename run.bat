@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake
chcp 65001 >nul 2>&1
color 0B

REM Optional: run.bat 1            → menus for continue/fresh + proxy
REM           run.bat 1 c          → continue lane 1, then proxy menu
REM           run.bat 1 f          → fresh wipe + lane 1, then proxy menu
REM           run.bat 1 c n        → continue + Netlox (default)
REM           run.bat 1 c d        → continue + direct (no proxy)
REM           run.bat 1 c i        → continue + ISP Manage Proxy pool
set "CHOICE=%~1"
set "MODE=%~2"
set "PROXY=%~3"
set "PROXY_FLAG="
set "PROXY_LABEL=Netlox"

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
if /i "%MODE%"=="C" goto :proxy_mode
if /i "%MODE%"=="c" goto :proxy_mode
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
goto :proxy_mode

:proxy_mode
if not "%PROXY%"=="" goto :proxy_dispatch
echo.
echo   +-------------------------------+------------------------------+
echo   ^|  LANE %CHOICE%  PROXY                    ^|  DETAIL                       ^|
echo   +-------------------------------+------------------------------+
echo   ^|  [N]  Netlox                  ^|  Sticky US inject ^(default^)  ^|
echo   ^|  [D]  Direct / no proxy       ^|  Skip Netlox, use machine IP ^|
echo   ^|  [I]  ISP                     ^|  Manage Proxy ISP pool       ^|
echo   ^|  [B]  Back                    ^|  Return to mode menu         ^|
echo   +-------------------------------+------------------------------+
echo.
set /p "PROXY=   Pick N / D / I / B: "

:proxy_dispatch
if /i "%PROXY%"=="B" (
  set "MODE="
  set "PROXY="
  set "PROXY_FLAG="
  set "PROXY_LABEL=Netlox"
  goto :lane_mode
)
if /i "%PROXY%"=="b" (
  set "MODE="
  set "PROXY="
  set "PROXY_FLAG="
  set "PROXY_LABEL=Netlox"
  goto :lane_mode
)
if /i "%PROXY%"=="N" (
  set "PROXY_FLAG="
  set "PROXY_LABEL=Netlox"
  goto :lane
)
if /i "%PROXY%"=="n" (
  set "PROXY_FLAG="
  set "PROXY_LABEL=Netlox"
  goto :lane
)
if /i "%PROXY%"=="" (
  set "PROXY_FLAG="
  set "PROXY_LABEL=Netlox"
  goto :lane
)
if /i "%PROXY%"=="D" (
  set "PROXY_FLAG=--no-proxy"
  set "PROXY_LABEL=Direct no-proxy"
  goto :lane
)
if /i "%PROXY%"=="d" (
  set "PROXY_FLAG=--no-proxy"
  set "PROXY_LABEL=Direct no-proxy"
  goto :lane
)
if /i "%PROXY%"=="I" (
  set "PROXY_FLAG=--isp"
  set "PROXY_LABEL=ISP pool"
  goto :lane
)
if /i "%PROXY%"=="i" (
  set "PROXY_FLAG=--isp"
  set "PROXY_LABEL=ISP pool"
  goto :lane
)

echo   [x] Invalid proxy: %PROXY%
pause
exit /b 1

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
echo   ^|  Proxy !PROXY_LABEL!                  ^|  stop at 300 good          ^|
echo   ^|  Dashboard :8787 background   ^|                              ^|
echo   +-------------------------------+------------------------------+
echo.

REM System proxy must not swallow localhost (common Netlox/VPS hang).
set "NO_PROXY=127.0.0.1,localhost,local.adspower.net,local.adspower.com"
set "no_proxy=%NO_PROXY%"

if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"
echo.>> "%~dp0data\logs\dashboard.log"
echo ===== dashboard start %DATE% %TIME% =====>> "%~dp0data\logs\dashboard.log"

REM Already up? Kill and restart so lane flags (inject_netlox) match this checkout.
"%VENVPY%" -c "import urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); o.open('http://127.0.0.1:8787/api/runs/current', timeout=2).read()" >nul 2>&1
if not errorlevel 1 (
  echo   [..] Restarting dashboard so proxy mode is applied...
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8787" ^| findstr "LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
  )
  timeout /t 2 /nobreak >nul
)

start "SnappyMake Dashboard" /min cmd /c "cd /d %~dp0 && .venv\Scripts\python.exe -m app >> data\logs\dashboard.log 2>&1"

echo   Waiting for dashboard ...
set "READY=0"
for /l %%i in (1,1,45) do (
  if "!READY!"=="0" (
    "%VENVPY%" -c "import urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); o.open('http://127.0.0.1:8787/api/runs/current', timeout=2).read()" >nul 2>&1
    if not errorlevel 1 set "READY=1"
  )
  if "!READY!"=="0" (
    if %%i==1 echo   attempt 1/45 ...
    if %%i==5 echo   still waiting 5/45 ...
    if %%i==15 echo   still waiting 15/45 ...
    if %%i==30 echo   still waiting 30/45 ...
    timeout /t 2 /nobreak >nul
  )
)
if not "!READY!"=="1" (
  echo   [x] Dashboard did not start on :8787
  echo   [!] Last dashboard log lines:
  powershell -NoProfile -Command "Get-Content -Path '%~dp0data\logs\dashboard.log' -Tail 25 -ErrorAction SilentlyContinue"
  echo.
  echo   Tip: close other python on 8787, open AdsPower, re-run.
  pause
  exit /b 1
)

echo   [ok] Dashboard up.
:lane_ready
echo.
echo   Launch: sms_create_lane.py --vps %CHOICE% --target 300 --ui !FRESH_FLAG! !PROXY_FLAG!
"%VENVPY%" scripts\sms_create_lane.py --vps %CHOICE% --target 300 --ui !FRESH_FLAG! !PROXY_FLAG!
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
