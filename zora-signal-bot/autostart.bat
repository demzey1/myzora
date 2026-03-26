@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: autostart.bat — Add Zora Signal Bot to Windows startup
:: Run this ONCE as Administrator to make the bot start automatically on boot
:: ─────────────────────────────────────────────────────────────────────────────

echo Setting up Zora Signal Bot to start automatically on Windows boot...
echo.

:: Get the directory where this batch file lives
set BOT_DIR=%~dp0

:: Create the startup script that will run on boot
set STARTUP_SCRIPT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\zora-bot-start.bat

echo @echo off > "%STARTUP_SCRIPT%"
echo cd /d "%BOT_DIR%" >> "%STARTUP_SCRIPT%"
echo docker compose up -d >> "%STARTUP_SCRIPT%"
echo echo Zora Signal Bot started >> "%STARTUP_SCRIPT%"

echo.
echo Done! The bot will now start automatically when Windows boots.
echo Startup script created at:
echo %STARTUP_SCRIPT%
echo.
echo To remove autostart, delete that file.
echo.
pause
