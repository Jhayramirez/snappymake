@echo off
setlocal
cd /d "%~dp0"
title SnappyMake - Enable auto-start

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\SnappyMake.lnk'); $s.TargetPath='%~dp0run.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Description='SnappyMake dashboard'; $s.Save()"

if exist "%STARTUP%\SnappyMake.lnk" (
  echo Done. SnappyMake will start automatically when you log in to Windows.
  echo (It still needs AdsPower open with Local API enabled.)
) else (
  echo [ERROR] Could not create the startup shortcut.
)
echo.
pause
endlocal
