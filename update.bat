@echo off
chcp 65001 >nul
title Aegis — Smart Routing & Multi-Subscription Merger
echo ========================================================
echo   Aegis: Updating VPN Subscriptions...
echo ========================================================
echo.
python "%~dp0sync.py"
echo.
echo ========================================================
echo   Done! Refresh or select profile in Clash Verge / FlClash.
echo ========================================================
echo.
pause
