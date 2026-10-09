@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   ĐANG TỰ ĐỘNG CÀI ĐẶT CÁC THƯ VIỆN PYTHON CẦN THIẾT
echo ============================================================
echo.

python -m pip install --upgrade -r requirements.txt

echo.
echo ============================================================
echo [V] ĐÃ CÀI ĐẶT XONG THƯ VIỆN! HÃY BẬT LẠI BOT.
echo ============================================================
echo.
pause
