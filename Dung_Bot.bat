@echo off
chcp 65001 >nul
title Tat Bot Khai Hoan Derma
echo ==============================================
echo       TAT BOT KHAI HOAN DERMA (NGAM)
echo ==============================================
echo Dang tim va tat cac tien trinh bot...

:: 1. Giai phong bat ky PID nao dang chiem cong 52365
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :52365') do (
    if not "%%a"=="" (
        echo Dang giai phong PID chiem cong: %%a
        taskkill /F /T /PID %%a >nul 2>&1
    )
)

:: 2. Dung tien trinh PowerShell va Python cua bot
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*telegram_woocommerce_bot.py*' -or $_.CommandLine -like '*run_telegram_bot.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Da dung tien trinh: ' + $_.ProcessId) }"

echo.
echo Da tat bot thanh cong! Cong 52365 da duoc giai phong hoan toan.
timeout /t 3
