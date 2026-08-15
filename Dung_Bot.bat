@echo off
chcp 65001 >nul
title Tắt Bot Khải Hoàn Derma
echo ==============================================
echo       TẮT BOT KHẢI HOÀN DERMA (NGẦM)
echo ==============================================
echo Đang tìm và tắt các tiến trình bot...

powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*telegram_woocommerce_bot.py*' -or $_.CommandLine -like '*run_telegram_bot.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Đã dừng tiến trình: ' + $_.ProcessId) }"

echo.
echo Đã tắt bot thành công!
timeout /t 3
