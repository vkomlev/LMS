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

# Обёртка .vbs: запускает PowerShell полностью без окна. Файл служебный,
# живёт вне репозитория и перезаписывается при каждой установке.
$workDir = Join-Path $env:LOCALAPPDATA 'MemuraiDev'
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$vbsPath = Join-Path $workDir 'ensure-redis-silent.vbs'
$vbsBody = @"
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$ensureRedis"" -Quiet", 0, False
"@
Set-Content -LiteralPath $vbsPath -Value $vbsBody -Encoding ASCII

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbsPath`""

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

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
    -Trigger @($logonTrigger, $repeatTrigger) `
    -Settings $settings `
    -Description 'tsk-611: держит локальный Redis (Memurai) поднятым для тестов и локального запуска LMS. Проверка при входе в систему и раз в несколько минут; если Redis отвечает — ничего не делает.' `
    -Force | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована: при входе в систему и каждые $IntervalMinutes мин."
Write-Host "Проверить: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
