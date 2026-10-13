@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   BẬT TỰ ĐỘNG CHẠY BOT VÀ MINI APP KHI MỞ MÁY TÍNH
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$dir = '%~dp0'.TrimEnd('\'); " ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$target = Join-Path $dir 'Chay_Ngam_Bot.vbs'; " ^
    "$startupFolder = [Environment]::GetFolderPath('Startup'); " ^
    "$shortcutPath = Join-Path $startupFolder 'Bot_Khai_Hoan_Derma.lnk'; " ^
    "$oldShortcut = Join-Path $startupFolder 'Chay_Ngam_Bot.vbs - Shortcut.lnk'; " ^
    "if (Test-Path $oldShortcut) { Remove-Item $oldShortcut -Force }; " ^
    "$s = $ws.CreateShortcut($shortcutPath); " ^
    "$s.TargetPath = $target; " ^
    "$s.WorkingDirectory = $dir; " ^
    "$s.Description = 'Tu dong chay ngam Bot va Mini App Khai Hoan Derma khi mo may'; " ^
    "$s.Save(); " ^
    "Write-Host ('[V] Da tao loi tat Startup thanh cong tai: ' + $shortcutPath) -ForegroundColor Green; " ^
    "wscript.exe `\"$target`\"; " ^
    "Write-Host '[V] Da khoi dong Bot chay ngam thanh cong!' -ForegroundColor Green"

echo.
echo ============================================================
echo [V] THÀNH CÔNG! Từ bây giờ:
echo - Bot da duoc kich hoat chay ngam ngay lap tuc tren may tinh.
echo - Moi khi ban bat may tinh len, Bot va Mini App se tu dong chay ngam 100%%.
echo - Ban khong can phai bam bat ky file nao nua.
echo ============================================================
echo.
pause
