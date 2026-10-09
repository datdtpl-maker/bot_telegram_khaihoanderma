@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   BẬT TỰ ĐỘNG CHẠY BOT VÀ MINI APP KHI MỞ MÁY TÍNH
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$target = Join-Path $PSScriptRoot 'Chay_Ngam_Bot.vbs'; " ^
    "$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Bot_Khai_Hoan_Derma.lnk'; " ^
    "$s = $ws.CreateShortcut($shortcutPath); " ^
    "$s.TargetPath = $target; " ^
    "$s.WorkingDirectory = $PSScriptRoot; " ^
    "$s.Description = 'Tu dong chay ngam Bot va Mini App Khai Hoan Derma khi mo may'; " ^
    "$s.Save(); " ^
    "Write-Host '[V] Da tao loi tat Startup thanh cong tai:' $shortcutPath -ForegroundColor Green"

echo.
echo ============================================================
echo [V] THÀNH CÔNG! Từ bây giờ:
echo - Mỗi khi bạn bật máy tính lên, Bot và Mini App sẽ tự động chạy ngầm 100%%.
echo - Bạn không cần phải bấm bất kỳ file nào nữa.
echo ============================================================
echo.
pause
