@echo off
title POL ADV — Notificador Desktop
cd /d "%~dp0"
echo ========================================================
echo   Iniciando POL ADV Desktop Notifier (Tray & Widgets)
echo ========================================================
echo.
python -m app.main
pause
