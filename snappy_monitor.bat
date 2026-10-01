@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMonitor
chcp 65001 >nul 2>&1
color 0B

if not "%~1"=="" goto :run

:menu
cls
echo.
echo   ============================================================
echo      S N A P P Y   M O N I T O R
echo   ============================================================
echo.
echo   Screenshots all open AdsPower browsers.
echo   Batch POST: profile + screenshot  -^>  Laravel
echo.
echo   Needs: AdsPower open  ^|  Local API :50325
echo   Config: SnappyMonitor\config.json
echo.
echo   ------------------------------------------------------------
echo     1^) Run loop ^(every 5 min^)
echo     2^) One cycle + POST
echo     3^) One cycle dry-run ^(local shots only^)
echo     4^) Edit config.json
echo     5^) Exit
echo   ------------------------------------------------------------
echo.
set "CHOICE="
set /p "CHOICE=  Choose [1-5]: "

if "%CHOICE%"=="1" goto :loop
if "%CHOICE%"=="2" goto :once
if "%CHOICE%"=="3" goto :dry
if "%CHOICE%"=="4" goto :config
if "%CHOICE%"=="5" exit /b 0
echo.
echo   Invalid choice.
timeout /t 1 >nul
goto :menu

:config
call :ensure_config
if exist "SnappyMonitor\config.json" (
  notepad "SnappyMonitor\config.json"
) else (
  echo   config.json missing.
  pause
)
goto :menu

:loop
set "ARGS="
goto :run

:once
set "ARGS=--once"
goto :run

:dry
set "ARGS=--once --dry-run"
goto :run

:run
cls
echo.
echo   ============================================================
echo      S N A P P Y   M O N I T O R
echo   ============================================================
echo.

call :ensure_venv
call :ensure_deps
if errorlevel 1 exit /b 1
call :ensure_config

echo   Ctrl+C to stop.
echo.
if defined ARGS (
  python SnappyMonitor\monitor.py %ARGS%
) else (
  python SnappyMonitor\monitor.py %*
)
set "EC=%ERRORLEVEL%"
echo.
if not "%EC%"=="0" (
  echo   Exited with code %EC%
)
pause
exit /b %EC%

:ensure_venv
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
  echo   [ok] venv
  goto :eof
)
if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
  echo   [ok] venv
  goto :eof
)
echo   [warn] no .venv — using system Python
goto :eof

:ensure_deps
where python >nul 2>&1
if errorlevel 1 (
  echo   [fail] Python not found on PATH
  pause
  exit /b 1
)

echo   [check] pillow / playwright / httpx ...
python -c "import PIL, playwright, httpx" 1>nul 2>nul
if errorlevel 1 (
  echo   [install] missing packages — installing now...
  python -m pip install -U pillow playwright httpx
  if errorlevel 1 (
    echo   [fail] pip install failed
    pause
    exit /b 1
  )
  python -c "import PIL, playwright, httpx" 1>nul 2>nul
  if errorlevel 1 (
    echo   [fail] packages still missing after install
    pause
    exit /b 1
  )
  echo   [ok] deps installed
) else (
  echo   [ok] deps
)
exit /b 0

:ensure_config
if exist "SnappyMonitor\config.json" goto :eof
if exist "SnappyMonitor\config.example.json" (
  copy /Y "SnappyMonitor\config.example.json" "SnappyMonitor\config.json" >nul
  echo   [ok] created config.json from example
) else (
  echo   [warn] no config.example.json
)
goto :eof
