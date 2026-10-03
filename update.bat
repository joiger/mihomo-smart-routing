@echo off
chcp 65001 >nul
title Mihomo Smart Routing Updater
echo ========================================================
echo   Updating VPN Subscriptions...
echo ========================================================
echo.
python "%~dp0sync.py"
echo.
echo ========================================================
echo   Done! Refresh or select profile in Clash Verge / FlClash.
echo ========================================================
echo.
pause
