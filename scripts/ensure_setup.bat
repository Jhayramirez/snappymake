@echo off
REM Shared Windows bootstrap:
REM   1) install Python if missing (winget or official silent installer)
REM   2) create .venv + pip + Playwright
REM Called from repo-root bats via: call "%~dp0scripts\ensure_setup.bat"
REM Sets ERRORLEVEL 1 on failure.

setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0.." >nul

chcp 65001 >nul 2>&1

set "VENVPY=%CD%\.venv\Scripts\python.exe"
set "NEED_SETUP=0"
set "PYEXE="

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

call :find_python
if not defined PYEXE (
  echo   [*] Python missing — installing Python 3.12 automatically...
  call :install_python
  call :refresh_path
  call :find_python
)

if not defined PYEXE (
  echo   [x] Could not install Python automatically.
  echo       Install Python 3.12+ manually, tick "Add python.exe to PATH", re-run.
  popd >nul
  endlocal
  exit /b 1
)

for /f "delims=" %%v in ('"%PYEXE%" --version 2^>^&1') do echo   [*] Using %%v  ^(%PYEXE%^)

if not exist "%VENVPY%" (
  echo   [1/3] Creating .venv ...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 (
    echo   [x] venv failed
    popd >nul
    endlocal
    exit /b 1
  )
) else (
  echo   [1/3] .venv already exists — reusing
)

echo   [2/3] pip install -r requirements.txt ...
"%VENVPY%" -m pip install --upgrade pip >nul
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   [x] pip install failed — check internet, then re-run
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


:find_python
set "PYEXE="
REM Prefer the py launcher (real install), then common install paths, then python on PATH.
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    if exist "%%p" set "PYEXE=%%p"
  )
)
if defined PYEXE goto :eof

for %%P in (
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python313\python.exe"
) do (
  if not defined PYEXE if exist %%~P (
    %%~P --version >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%~P"
  )
)
if defined PYEXE goto :eof

where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PYEXE (
      echo %%p | find /i "WindowsApps" >nul
      if errorlevel 1 (
        "%%p" --version >nul 2>&1
        if not errorlevel 1 set "PYEXE=%%p"
      )
    )
  )
)
goto :eof


:install_python
REM 1) winget (fastest on modern Windows / Server)
where winget >nul 2>&1
if not errorlevel 1 (
  echo   [+] winget install Python.Python.3.12 ...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
  if not errorlevel 1 goto :eof
  echo   [!] winget failed — trying official installer
)

REM 2) Official silent installer (per-user, adds PATH)
set "PY_VER=3.12.8"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PY_SETUP=%TEMP%\snappy-python-%PY_VER%-amd64.exe"
echo   [+] Downloading %PY_URL%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_SETUP%' -UseBasicParsing; exit 0 } catch { Write-Host $_; exit 1 }"
if errorlevel 1 (
  echo   [x] Download failed
  goto :eof
)
echo   [+] Silent install (PrependPath=1) ...
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 SimpleInstall=1
set "RC=!ERRORLEVEL!"
del /f /q "%PY_SETUP%" >nul 2>&1
if not "%RC%"=="0" echo   [!] Installer exit code %RC%
goto :eof


:refresh_path
REM Pull Machine + User PATH into this CMD so newly installed python is visible.
set "SYSPATH="
set "USERPATH="
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYSPATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USERPATH=%%B"
if defined SYSPATH if defined USERPATH (
  set "PATH=%SYSPATH%;%USERPATH%"
) else if defined SYSPATH (
  set "PATH=%SYSPATH%;%PATH%"
) else if defined USERPATH (
  set "PATH=%USERPATH%;%PATH%"
)
REM Also prepend common Python folders just in case PrependPath lagged.
if exist "%LocalAppData%\Programs\Python\Python312" set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
if exist "%LocalAppData%\Programs\Python\Python313" set "PATH=%LocalAppData%\Programs\Python\Python313;%LocalAppData%\Programs\Python\Python313\Scripts;%PATH%"
goto :eof
