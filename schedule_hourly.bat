@echo off
chcp 65001 >nul
echo ========================================================
echo Настройка ежечасного автообновления подписок Aegis...
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$scriptPath = (Get-Item '%~dp0sync.py').FullName; $scriptDir = Split-Path $scriptPath -Parent; $py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }; if (-not $py) { $py = 'pythonw.exe' }; $action = New-ScheduledTaskAction -Execute $py -Argument ('\"' + $scriptPath + '\"') -WorkingDirectory $scriptDir; $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1); $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10); Register-ScheduledTask -TaskName 'AegisSmartRoutingSync' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null; Write-Output '[OK] Задача Aegis в планировщике Windows успешно зарегистрирована!'"

if %errorlevel% equ 0 (
    echo.
    echo Задача настроена! Aegis будет тихо обновлять подписки каждый час в фоне.
) else (
    echo.
    echo [Ошибка] Не удалось зарегистрировать задачу. Попробуйте запустить от имени Администратора.
)

echo.
pause
