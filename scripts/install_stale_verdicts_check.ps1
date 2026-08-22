<#
.SYNOPSIS
    Зарегистрировать еженедельную проверку устаревших незачётов в планировщике Windows.

.DESCRIPTION
    tsk-636. Проверка ищет незачёты, которые нынешние правила заданий признали бы
    зачётами (правку эталона старые вердикты не пересчитывает). Находку 8 августа
    2026 никто не заметил две недели — расхождение не падает, не пишется в лог и не
    видно на экране, поэтому искать его должен планировщик.

    Задача ставится в контексте текущего пользователя, права администратора не нужны.

    ТИХО, без мигающих окон: задача запускает `pythonw.exe` из venv проекта. Это
    GUI-программа, консоль ей не выделяется вовсе — окна не будет ни на мгновение.
    Итог прогона пишется в logs\stale_verdicts_check.log.

    Почему не powershell.exe, как у соседних чеков: он консольный, планировщик
    создаёт ему окно и лишь потом прячет по `-WindowStyle Hidden`, поэтому раз в
    неделю на экране моргает чёрный прямоугольник. Соседям обёртка на PowerShell
    нужна ради подстановки прод-DSN в DATABASE_URL; здесь этого не требуется —
    аудит читает подключение из .mcp.json сам.

    Почему не S4U («выполнять независимо от того, вошёл ли пользователь», тоже без
    окна): регистрация такой задачи требует права «Вход в качестве пакетного
    задания» и без администратора отвечает «Access is denied» (проверено 22.08.2026).

    Обёртка .vbs (ещё один рецепт «совсем без окна») здесь НЕ используется намеренно:
    в этом проекте её уже пробовали для другой задачи и откатили — wscript.exe после
    `shell.Run` не завершался, задача навсегда оставалась в состоянии «выполняется», а
    при MultipleInstances=IgnoreNew это глушило все последующие срабатывания. См.
    комментарий в scripts/dev/install-redis-autostart.ps1.

    Время — понедельник 09:20, следом за соседними чеками (09:00 порядок разделов,
    09:10 непроверяемые задания), чтобы три прогона не лезли в прод-базу разом.

.PARAMETER TaskName
    Имя задачи. По умолчанию «LMS stale verdicts weekly». Латиницей намеренно:
    у соседней задачи кириллица в имени доставила хлопот (см. «chek poryadka razdelov»).

.PARAMETER At
    Время запуска в понедельник. По умолчанию 09:20.

.PARAMETER Uninstall
    Снять задачу вместо установки.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_stale_verdicts_check.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_stale_verdicts_check.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'LMS stale verdicts weekly',
    [string]$At = '09:20',
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

$repo = Split-Path -Parent $PSScriptRoot
$check = Join-Path $repo 'scripts\stale_verdicts_weekly.py'
if (-not (Test-Path -LiteralPath $check)) {
    Write-Error "Не найден $check"
    exit 1
}

# Именно pythonw.exe, а не python.exe: см. .DESCRIPTION. Системный интерпретатор не
# подойдёт — в нём нет зависимостей проекта, поэтому venv обязателен, и его отсутствие
# должно быть внятной ошибкой сейчас, а не молчаливым провалом раз в неделю.
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Error "Не найден $pythonw — создайте venv проекта перед установкой задачи."
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "`"$check`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $At

# StartWhenAvailable: пропущенный понедельник (машина была выключена) догоняется,
# иначе неделя молча выпадает — а именно «никто не заметил» и есть чинимая беда.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -Hidden

$description = 'tsk-636: ищет незачёты, которые нынешние правила заданий признали бы зачётами (правка эталона старые вердикты не пересчитывает). Только чтение прод-базы. Итог — logs\stale_verdicts_check.log; при отсутствии находок чек молчит.'

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description $description -Force | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована: понедельник $At, без окна."
Write-Host "Проверить:  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Прогнать:   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Журнал:     $(Join-Path $repo 'logs\stale_verdicts_check.log')"
