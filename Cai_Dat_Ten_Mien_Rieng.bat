@echo off
chcp 65001 >nul
title CAU HINH TEN MIEN RIENG CHO MINI APP
cd /d "%~dp0"

echo ======================================================
echo    TIEN ICH CAU HINH TEN MIEN RIENG CHO MINI APP
echo ======================================================
echo.
python setup_custom_domain.py
echo.
echo ======================================================
echo Dang khoi dong lai Bot voi ten mien moi...
call Dung_Bot.bat
timeout /t 2 >nul
start /b python telegram_woocommerce_bot.py
echo Da khoi dong Bot voi ten mien co dinh!
pause
