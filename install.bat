@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SnappyMake - Windows Setup
chcp 65001 >nul 2>&1
color 0B

cls
echo.
echo   ============================================================
echo      S N A P P Y M A K E   ·   o n e - c l i c k   s e t u p
echo   ============================================================
echo.
echo   Installs Python (if needed), .venv, packages, Playwright,
echo   then starts the dashboard.
echo.

call "%~dp0scripts\ensure_setup.bat"
if errorlevel 1 (
  echo.
  echo   [x] Setup failed.
  echo.
  pause
  exit /b 1
)

echo   Starting dashboard...
echo.
timeout /t 2 >nul
call "%~dp0run.bat"
endlocal
