@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   TẮT TỰ ĐỘNG CHẠY BOT KHI MỞ MÁY TÍNH
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$startup = [Environment]::GetFolderPath('Startup'); " ^
    "$s1 = Join-Path $startup 'Bot_Khai_Hoan_Derma.lnk'; " ^
    "$s2 = Join-Path $startup 'Chay_Ngam_Bot.vbs - Shortcut.lnk'; " ^
    "$removed = $false; " ^
    "if (Test-Path $s1) { Remove-Item $s1 -Force; $removed = $true }; " ^
    "if (Test-Path $s2) { Remove-Item $s2 -Force; $removed = $true }; " ^
    "if ($removed) { Write-Host '[V] Da xoa loi tat khoi dong khoi thu muc Startup.' -ForegroundColor Yellow } else { Write-Host '[!] Bot chua tung duoc cai dat tu dong khoi dong.' }"

echo.
echo ============================================================
echo [V] Đã tắt chế độ tự động chạy khi mở máy thành công.
echo ============================================================
echo.
pause
