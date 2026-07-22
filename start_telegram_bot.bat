@echo off
setlocal
chcp 65001 >nul
title Bot Khải Hoàn Derma
cd /d "%~dp0"

echo ==============================================
echo       KHỞI ĐỘNG BOT KHẢI HOÀN DERMA
echo ==============================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_telegram_bot.ps1"
set "BOT_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%BOT_EXIT_CODE%"=="0" (
    echo Bot dừng do lỗi. Hãy kiểm tra bot.log và nội dung thông báo phía trên.
) else (
    echo Bot đã dừng.
)
pause
exit /b %BOT_EXIT_CODE%
