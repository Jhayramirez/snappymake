@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SNAPPY MONITOR  //  live wall
chcp 65001 >nul 2>&1

REM Enable ANSI / VT colors on modern Windows consoles
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1
for /f %%a in ('echo prompt $E^| cmd') do set "ESC=%%a"

set "C0=%ESC%[0m"
set "DIM=%ESC%[2m"
set "BOLD=%ESC%[1m"
set "MINT=%ESC%[38;2;61;255;181m"
set "CYAN=%ESC%[38;2;80;220;255m"
set "HOT=%ESC%[38;2;255;80;120m"
set "GOLD=%ESC%[38;2;255;176;32m"
set "SOFT=%ESC%[38;2;160;170;185m"
set "WHITE=%ESC%[97m"
set "BG=%ESC%[48;2;10;12;16m"
set "OK=%ESC%[38;2;61;255;181m"
set "WARN=%ESC%[38;2;255;176;32m"
set "FAIL=%ESC%[38;2;255;80;120m"

mode con cols=92 lines=40 >nul 2>&1
color 0B

if not "%~1"=="" (
  set "ARGS=%*"
  goto :run
)

:menu
cls
call :banner
echo   %DIM%  ┌──────────────────────────────────────────────────────────────────────────┐%C0%
echo   %DIM%  │%C0%  %SOFT%capture every open AdsPower browser  →  JPEG  →  Laravel wall%C0%          %DIM%│%C0%
echo   %DIM%  │%C0%  %SOFT%needs:%C0% AdsPower Local API %MINT%:50325%C0%   %SOFT%config:%C0% SnappyMonitor\config.json   %DIM%│%C0%
echo   %DIM%  └──────────────────────────────────────────────────────────────────────────┘%C0%
echo.
echo   %MINT%  ▸ OPERATIONS%C0%
echo.
echo      %CYAN%[%WHITE%1%CYAN%]%C0%  %BOLD%RUN LOOP%C0%          %DIM%───%C0%  every 5 min · collect → post · forever
echo      %CYAN%[%WHITE%2%CYAN%]%C0%  %BOLD%ONE SHOT%C0%          %DIM%───%C0%  single cycle + POST to 21snaps
echo      %CYAN%[%WHITE%3%CYAN%]%C0%  %BOLD%DRY RUN%C0%           %DIM%───%C0%  local shots only · no upload
echo      %CYAN%[%WHITE%4%CYAN%]%C0%  %BOLD%CONFIG%C0%            %DIM%───%C0%  edit post_url / token
echo      %CYAN%[%WHITE%5%CYAN%]%C0%  %BOLD%EXIT%C0%
echo.
echo   %DIM%  ──────────────────────────────────────────────────────────────────────────%C0%
echo.
set "CHOICE="
set /p "CHOICE=  %MINT%»%C0%  choose %DIM%[1-5]%C0%  %MINT%›%C0% "

if "%CHOICE%"=="1" goto :loop
if "%CHOICE%"=="2" goto :once
if "%CHOICE%"=="3" goto :dry
if "%CHOICE%"=="4" goto :config
if "%CHOICE%"=="5" (
  cls
  echo.
  echo   %DIM%  connection closed.%C0%
  echo.
  exit /b 0
)
echo.
echo   %FAIL%  ✕ invalid · try again%C0%
timeout /t 1 >nul
goto :menu

:config
call :ensure_config
cls
call :banner
echo   %GOLD%  ▸ opening config.json%C0%
echo.
if exist "SnappyMonitor\config.json" (
  notepad "SnappyMonitor\config.json"
) else (
  echo   %FAIL%  ✕ config.json missing%C0%
  pause
)
goto :menu

:loop
set "ARGS="
set "MODE_LABEL=LIVE LOOP  ·  5 MIN CADENCE"
goto :run

:once
set "ARGS=--once"
set "MODE_LABEL=ONE SHOT  ·  POST"
goto :run

:dry
set "ARGS=--once --dry-run"
set "MODE_LABEL=DRY RUN  ·  LOCAL ONLY"
goto :run

:run
cls
call :banner
if not defined MODE_LABEL set "MODE_LABEL=CUSTOM"
echo   %GOLD%  ▸ MODE%C0%  %BOLD%%WHITE%%MODE_LABEL%%C0%
echo.
call :bootline "warming console" 0
call :ensure_venv
call :bootline "locking python env" 0
call :ensure_deps
if errorlevel 1 (
  echo.
  echo   %FAIL%  ✕ SYSTEMS RED  ·  fix deps then retry%C0%
  echo.
  pause
  exit /b 1
)
call :bootline "syncing config" 0
call :ensure_config
call :bootline "armed" 0
echo.
echo   %DIM%  ┌──────────────────────────────────────────────────────────────────────────┐%C0%
echo   %DIM%  │%C0%  %MINT%●%C0%  LIVE     %SOFT%Ctrl+C kills the loop%C0%                                    %DIM%│%C0%
echo   %DIM%  │%C0%  %SOFT%target%C0%   https://21snaps.laravel.cloud/api/browser-shots              %DIM%│%C0%
echo   %DIM%  └──────────────────────────────────────────────────────────────────────────┘%C0%
echo.
echo   %MINT%  ═══════════════  TRANSMISSION START  ═══════════════%C0%
echo.

if defined ARGS (
  python SnappyMonitor\monitor.py %ARGS%
) else (
  python SnappyMonitor\monitor.py %*
)
set "EC=%ERRORLEVEL%"
echo.
echo   %MINT%  ═══════════════  TRANSMISSION END  ═══════════════%C0%
echo.
if not "%EC%"=="0" (
  echo   %FAIL%  ✕ exit code %EC%%C0%
) else (
  echo   %OK%  ✓ clean exit%C0%
)
echo.
pause
exit /b %EC%

:banner
echo %BG%
echo   %MINT%  ███████╗███╗   ██╗ █████╗ ██████╗ ██████╗ ██╗   ██╗%C0%
echo   %MINT%  ██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝%C0%
echo   %CYAN%  ███████╗██╔██╗ ██║███████║██████╔╝██████╔╝ ╚████╔╝ %C0%
echo   %CYAN%  ╚════██║██║╚██╗██║██╔══██║██╔═══╝ ██╔═══╝   ╚██╔╝  %C0%
echo   %SOFT%  ███████║██║ ╚████║██║  ██║██║     ██║        ██║   %C0%
echo   %SOFT%  ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝     ╚═╝        ╚═╝   %C0%
echo.
echo   %BOLD%%WHITE%  M O N I T O R%C0%  %DIM%//%C0%  %MINT%adspower lens%C0%  %DIM%//%C0%  %CYAN%laravel wall%C0%
echo   %DIM%  ──────────────────────────────────────────────────────────────────────────%C0%
echo %C0%
goto :eof

:bootline
set "MSG=%~1"
echo   %DIM%  [%C0%%MINT%■%C0%%DIM%]%C0%  %SOFT%%MSG%...%C0%
timeout /t 0 >nul
goto :eof

:ensure_venv
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
  echo   %OK%  ✓%C0%  %SOFT%venv locked%C0%
  goto :eof
)
if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
  echo   %OK%  ✓%C0%  %SOFT%venv locked%C0%
  goto :eof
)
echo   %WARN%  !%C0%  %SOFT%no .venv — system Python%C0%
goto :eof

:ensure_deps
where python >nul 2>&1
if errorlevel 1 (
  echo   %FAIL%  ✕ Python not on PATH%C0%
  pause
  exit /b 1
)

echo   %DIM%  [·]%C0%  %SOFT%scanning pillow / playwright / httpx%C0%
python -c "import PIL, playwright, httpx" 1>nul 2>nul
if errorlevel 1 (
  echo   %GOLD%  ▸ packages missing — auto installing%C0%
  echo   %DIM%  ─────────────────────────────────────%C0%
  python -m pip install -U pillow playwright httpx
  if errorlevel 1 (
    echo   %FAIL%  ✕ pip install failed%C0%
    pause
    exit /b 1
  )
  python -c "import PIL, playwright, httpx" 1>nul 2>nul
  if errorlevel 1 (
    echo   %FAIL%  ✕ still missing after install%C0%
    pause
    exit /b 1
  )
  echo   %OK%  ✓%C0%  %SOFT%deps installed + verified%C0%
) else (
  echo   %OK%  ✓%C0%  %SOFT%deps green%C0%
)
exit /b 0

:ensure_config
if exist "SnappyMonitor\config.json" (
  echo   %OK%  ✓%C0%  %SOFT%config online%C0%
  goto :eof
)
if exist "SnappyMonitor\config.example.json" (
  copy /Y "SnappyMonitor\config.example.json" "SnappyMonitor\config.json" >nul
  echo   %OK%  ✓%C0%  %SOFT%config spawned from example%C0%
) else (
  echo   %WARN%  !%C0%  %SOFT%no config.example.json%C0%
)
goto :eof
