@echo off
setlocal
chcp 65001 >nul
title Bot Khai Hoan Derma
cd /d "%~dp0"

echo ==============================================
echo       KHOI DONG BOT KHAI HOAN DERMA
echo ==============================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_telegram_bot.ps1"
set "BOT_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%BOT_EXIT_CODE%"=="0" (
    echo Bot dung do loi. Hay kiem tra bot.log va noi dung phia tren.
) else (
    echo Bot da dung.
)
pause
exit /b %BOT_EXIT_CODE%
