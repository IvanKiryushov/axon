@echo off
chcp 65001 >nul
title AxonBot Remote Review Dashboard & Cloudflare Tunnel
echo ========================================================
echo  AxonBot OPSEC Remote Mobile Review Dashboard
echo ========================================================
echo.
echo Запуск локального сервера (порт 8788) и защищенного HTTPS-туннеля...
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0Scripts\media_sanitizer.py" --serve --port 8788 --tunnel --no-browser
pause
