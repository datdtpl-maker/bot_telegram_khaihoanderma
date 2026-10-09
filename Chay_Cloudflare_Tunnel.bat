@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   TẠO ĐƯỜNG DẪN HTTPS CHO TELEGRAM MINI APP (CLOUDFLARE)
echo ============================================================
echo.

where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Chưa tìm thấy công cụ cloudflared trên máy.
    echo.
    echo Bạn có thể tải cloudflared.exe miễn phí tại:
    echo https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
    echo (Đổi tên thành cloudflared.exe và đặt vào thư mục bot này hoặc cho vào PATH)
    echo.
    pause
    exit /b 1
)

echo [*] Đang khởi chạy Cloudflare Tunnel trỏ tới cổng 8088...
echo [*] Hãy copy URL dạng: https://xxxx.trycloudflare.com
echo [*] và dán vào biến MINI_APP_URL trong file telegram_bot.env
echo.
cloudflared tunnel --url http://localhost:8088

pause
