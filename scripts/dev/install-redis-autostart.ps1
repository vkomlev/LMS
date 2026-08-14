<#
.SYNOPSIS
    Зарегистрировать задачу планировщика, которая держит локальный Redis поднятым.

.DESCRIPTION
    tsk-611. Одного автозапуска службы мало: бесплатная Developer-редакция
    Memurai сама себя выключает после нескольких суток непрерывной работы, и
    дальше остаётся выключенной до ручного вмешательства. Поэтому задача
    вызывает ensure-redis.ps1 не только при входе в систему, но и раз в
    15 минут. Скрипт идемпотентный: если Redis отвечает — он не делает ничего,
    поэтому повторные срабатывания ничего не стоят.

    Задача регистрируется в контексте текущего пользователя — права
    администратора не нужны. Чтобы окно консоли не мигало каждые 15 минут,
    вызов идёт через маленькую обёртку .vbs в %LOCALAPPDATA%\MemuraiDev.

.PARAMETER TaskName
    Имя задачи в планировщике. По умолчанию «LMS Dev Redis».

.PARAMETER IntervalMinutes
    Как часто перепроверять Redis. По умолчанию 15 минут.

.PARAMETER Uninstall
    Снять задачу вместо установки.

.EXAMPLE
    .\scripts\dev\install-redis-autostart.ps1

.EXAMPLE
    .\scripts\dev\install-redis-autostart.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'LMS Dev Redis',
    [int]$IntervalMinutes = 15,
    [switch]$Uninstall
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача '$TaskName' снята."
    } else {
        Write-Host "Задачи '$TaskName' и не было."
    }
    exit 0
}

$ensureRedis = Join-Path $PSScriptRoot 'ensure-redis.ps1'
if (-not (Test-Path -LiteralPath $ensureRedis)) {
    Write-Error "Не найден $ensureRedis"
    exit 1
}

# Запуск PowerShell напрямую. Обёртка .vbs (вариант «совсем без мигания окна»)
# проверку не прошла: wscript.exe после `shell.Run` не завершался, задача
# оставалась в состоянии «выполняется», а при MultipleInstances=IgnoreNew это
# глушило все последующие срабатывания — то есть автоподъём молча не работал.
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ensureRedis`" -Quiet"

# Повтор внутри суток. Бесконечную длительность ([TimeSpan]::MaxValue) планировщик
# не принимает — «value is incorrectly formatted or out of range», поэтому окно
# ровно сутки, а заводится оно заново каждый день (ежедневный триггер ниже) и при
# каждом входе в систему.
$repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 1)).Repetition

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logonTrigger.Repetition = $repetition

$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '00:05'
$dailyTrigger.Repetition = $repetition

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -Hidden

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($logonTrigger, $dailyTrigger) `
    -Settings $settings `
    -Description 'tsk-611: держит локальный Redis (Memurai) поднятым для тестов и локального запуска LMS. Проверка при входе в систему и раз в несколько минут; если Redis отвечает — ничего не делает.' `
    -Force | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована: при входе в систему и каждые $IntervalMinutes мин."
Write-Host "Проверить: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
