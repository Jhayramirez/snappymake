@echo off
REM Shared Windows bootstrap: create .venv + install deps if missing.
REM Called from repo-root bats via: call "%~dp0scripts\ensure_setup.bat"
REM Sets ERRORLEVEL 1 on failure.

setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0.." >nul

chcp 65001 >nul 2>&1

set "VENVPY=%CD%\.venv\Scripts\python.exe"
set "NEED_SETUP=0"

if not exist "%VENVPY%" set "NEED_SETUP=1"
if not exist "%CD%\.venv\Lib\site-packages\playwright" set "NEED_SETUP=1"

if "%NEED_SETUP%"=="0" (
  "%VENVPY%" -c "import fastapi, uvicorn, httpx, playwright" >nul 2>&1
  if errorlevel 1 set "NEED_SETUP=1"
)

if "%NEED_SETUP%"=="0" (
  popd >nul
  endlocal
  exit /b 0
)

echo.
echo   [*] First-time / missing deps — running setup...
echo.

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo   [x] Python not found. Install Python 3.12+ and tick Add to PATH.
  popd >nul
  endlocal
  exit /b 1
)

if not exist "%VENVPY%" (
  echo   [1/3] Creating .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [x] venv failed
    popd >nul
    endlocal
    exit /b 1
  )
)

echo   [2/3] pip install -r requirements.txt ...
"%VENVPY%" -m pip install --upgrade pip >nul
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   [x] pip install failed
  popd >nul
  endlocal
  exit /b 1
)

echo   [3/3] Playwright Chromium ...
"%VENVPY%" -m playwright install chromium
if errorlevel 1 (
  echo   [!] Playwright install warned — continuing
)

echo   [ok] Setup ready
echo.
popd >nul
endlocal
exit /b 0
