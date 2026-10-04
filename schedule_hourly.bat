@echo off
chcp 65001 >nul
echo ========================================================
echo Настройка ежечасного автообновления подписок Mihomo...
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$scriptPath = (Get-Item '%~dp0sync.py').FullName; $action = New-ScheduledTaskAction -Execute 'pythonw.exe' -Argument ('\"' + $scriptPath + '\"'); $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1); $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10); Register-ScheduledTask -TaskName 'MihomoSmartRoutingSync' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null; Write-Output '[OK] Задача планировщика Windows успешно зарегистрирована!'"

if %errorlevel% equ 0 (
    echo.
    echo Задача настроена! Скрипт будет тихо обновлять подписки каждый час в фоне.
) else (
    echo.
    echo [Ошибка] Не удалось зарегистрировать задачу. Попробуйте запустить от имени Администратора.
)

echo.
pause
