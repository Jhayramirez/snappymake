@echo off
setlocal
title SnappyMake - Disable auto-start
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SnappyMake.lnk" 2>nul
echo Auto-start disabled. SnappyMake will no longer launch on login.
echo.
pause
endlocal
