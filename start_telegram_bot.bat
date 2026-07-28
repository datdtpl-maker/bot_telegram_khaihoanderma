@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_telegram_bot.ps1" %*
set "BOT_EXIT_CODE=%ERRORLEVEL%"

pause
exit /b %BOT_EXIT_CODE%
