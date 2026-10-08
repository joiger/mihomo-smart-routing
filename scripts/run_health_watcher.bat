@echo off
title Antigravity Health Dot Monitor
cd /d "%~dp0\.."
echo Запуск мониторинга работоспособности Antigravity...
node scripts\ag_health_watcher.mjs
pause
