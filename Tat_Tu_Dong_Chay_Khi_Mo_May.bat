@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   TẮT TỰ ĐỘNG CHẠY BOT KHI MỞ MÁY TÍNH
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Bot_Khai_Hoan_Derma.lnk'; " ^
    "if (Test-Path $shortcutPath) { Remove-Item $shortcutPath -Force; Write-Host '[V] Da xoa loi tat khoi dong khoi thu muc Startup.' -ForegroundColor Yellow } else { Write-Host '[!] Bot chua tung duoc cai dat tu dong khoi dong.' }"

echo.
echo ============================================================
echo [V] Đã tắt chế độ tự động chạy khi mở máy thành công.
echo ============================================================
echo.
pause
