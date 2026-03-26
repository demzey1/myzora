@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: run-background.bat — Start the bot in the background
:: Double-click this to start. Terminal can be closed after.
:: ─────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo Starting Zora Signal Bot...
docker compose up -d

echo.
echo Bot is running in the background.
echo.
echo Useful commands:
echo   docker compose logs -f api     (see live logs)
echo   docker compose ps              (check all services)
echo   docker compose down            (stop everything)
echo.
echo You can close this window. The bot will keep running.
echo.
pause
