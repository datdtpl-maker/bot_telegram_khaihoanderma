@echo off
setlocal
chcp 65001 >nul
title Cai Dat Cloudflared Cho Mini App
echo ============================================================
echo       CÀI ĐẶT CLOUDFLARED CHO TELEGRAM MINI APP
echo ============================================================
echo.

where cloudflared >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Máy tính đã có sẵn cloudflared trong PATH hệ thống!
    cloudflared --version
    echo.
    pause
    exit /b 0
)

if exist "%~dp0cloudflared.exe" (
    echo [OK] Đã có file cloudflared.exe trong thư mục bot!
    "%~dp0cloudflared.exe" --version
    echo.
    pause
    exit /b 0
)

echo [*] Đang tải cloudflared.exe từ GitHub chính thức...
curl -Lo "%~dp0cloudflared.exe" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
if %ERRORLEVEL% EQU 0 (
    echo [OK] Đã tải thành công cloudflared.exe vào thư mục bot!
    "%~dp0cloudflared.exe" --version
) else (
    echo [!] Lỗi khi tải. Hãy kiểm tra kết nối mạng hoặc tải thủ công tại:
    echo     https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
)

echo.
pause
