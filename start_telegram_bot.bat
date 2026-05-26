@echo off
setlocal
title Khai Hoan Telegram Bot
cd /d "%~dp0"
echo Dang khoi dong Bot Khai Hoan Derma...
echo Thu muc: %cd%
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_telegram_bot.ps1"
echo.
echo Bot da dung hoac gap loi. Kiem tra file bot.log trong thu muc nay neu can.
pause
