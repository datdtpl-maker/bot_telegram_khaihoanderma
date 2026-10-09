@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   KHỞI CHẠY TELEGRAM MINI APP SERVER (FASTAPI + UVICORN)
echo ============================================================

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

python -m uvicorn mini_app_server:app --host 0.0.0.0 --port 8088 --reload

pause
