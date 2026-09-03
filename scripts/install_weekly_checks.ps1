<#
.SYNOPSIS
    Завести (или обновить) задачи планировщика для еженедельных чеков прода LMS.

.DESCRIPTION
    tsk-641. Чеки ищут то, что не падает, не пишется в лог и не видно на экране:
    непроверяемые задания (tsk-361), сбитый порядок разделов (tsk-237), задания без
    файла-приложения (tsk-369), незачёты, устаревшие после правки эталона (tsk-636).
    Такое находит планировщик, а не случайный разбор.

    Все задачи запускают `pythonw.exe` из venv проекта. Это GUI-программа, консоль ей
    не выделяется вовсе — окна не будет ни на мгновение. Прежние обёртки на PowerShell
    мигали чёрным прямоугольником каждый понедельник: `powershell.exe` консольный,
    планировщик создаёт ему окно и лишь потом прячет по `-WindowStyle Hidden`.

    Тип входа S4U («выполнять независимо от того, вошёл ли пользователь», тоже без
    окна) не используется: его регистрация требует права «Вход в качестве пакетного
    задания» и без администратора отвечает `Access is denied` (проверено 22.08.2026).

    Обёртка .vbs — ещё один рецепт «совсем без окна» — в этом проекте уже пробовалась
    для задачи с Redis и была откатена: wscript.exe после `shell.Run` не завершался,
    задача навсегда оставалась в состоянии «выполняется», а при
    MultipleInstances=IgnoreNew это глушило все последующие срабатывания. См.
    scripts/dev/install-redis-autostart.ps1.

    Время разнесено по десять минут: три-четыре прогона не должны лезть в боевую базу
    разом. Пропущенный понедельник (машина была выключена) догоняется.

    Сводка (tsk-778). Отдельная задача `LMS weekly checks digest` в 09:45 читает итоги
    сегодняшних прогонов из logs\weekly_checks.log и шлёт оператору одно сообщение в
    Telegram — но только если есть находки, сбой чека или молчащий чек. Чистая неделя
    проходит молча: сводка «всё хорошо» каждый понедельник за месяц стала бы таким же
    непрочитанным фоном, как сами журналы.

    Как читать результат задачи (tsk-777). `LastTaskResult = 0` — чек отработал; находки
    при этом могли быть, их надо смотреть в журнале. Ненулевой результат означает ровно
    одно: чек не дошёл до конца. Раньше находки возвращались кодом 1, и четыре задачи из
    пяти месяцами стояли «с ошибкой», работая штатно, — на таком фоне настоящий сбой не
    отличить. Куда смотреть: logs\weekly_checks.log — по строке на каждый прогон
    («чисто» / «ЕСТЬ НАХОДКИ» / «СБОЙ» и куда идти за подробностями).

    Имена задач намеренно оставлены прежними — включая исторически кривое
    «LMS - chek poryadka razdelov (tsk-237)»: переименование ничего не чинит, а
    оператора, привыкшего искать задачу глазами, сбивает.

.PARAMETER Only
    Завести только одну задачу — по имени чека (ungradable, section-order,
    missing-attachments, stale-verdicts, slow-requests, tutor-outcomes, external-media)
    либо `digest` — сводку в Telegram. По умолчанию заводятся все, кроме
    missing-attachments (см. -WithMissingAttachments).

.PARAMETER WithMissingAttachments
    Добавить чек файлов-приложений. Отдельным флагом, потому что раньше он в
    планировщике не стоял: скрипт и обёртка были, задачи не было. Включать его молча
    — значит добавить оператору еженедельный отчёт, которого он не просил.

.PARAMETER Uninstall
    Снять задачи вместо установки.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_weekly_checks.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_weekly_checks.ps1 -Only stale-verdicts

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_weekly_checks.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [ValidateSet('ungradable', 'section-order', 'missing-attachments', 'stale-verdicts', 'slow-requests', 'tutor-outcomes', 'external-media', 'digest')]
    [string]$Only,
    [switch]$WithMissingAttachments,
    [switch]$Uninstall
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

# Чек → имя задачи и время в понедельник. Имена — те же, что стояли до tsk-641.
$plan = @(
    [pscustomobject]@{ Check = 'section-order';       TaskName = 'LMS - chek poryadka razdelov (tsk-237)'; At = '09:00'; Default = $true;  Why = 'порядок разделов курсов (tsk-237)' }
    [pscustomobject]@{ Check = 'ungradable';          TaskName = 'LMS ungradable tasks weekly';            At = '09:10'; Default = $true;  Why = 'задания, которые невозможно проверить (tsk-361)' }
    [pscustomobject]@{ Check = 'stale-verdicts';      TaskName = 'LMS stale verdicts weekly';              At = '09:20'; Default = $true;  Why = 'незачёты, устаревшие после правки эталона (tsk-636)' }
    [pscustomobject]@{ Check = 'slow-requests';       TaskName = 'LMS slow requests weekly';               At = '09:25'; Default = $true;  Why = 'запросы, которые ученик ждал дольше порога (tsk-644)' }
    [pscustomobject]@{ Check = 'tutor-outcomes';      TaskName = 'LMS tutor outcomes weekly';              At = '09:28'; Default = $true;  Why = 'чем кончаются разговоры с ИИ-наставником (tsk-661)' }
    [pscustomobject]@{ Check = 'external-media';      TaskName = 'LMS external media weekly';              At = '09:32'; Default = $true;  Why = 'картинки заданий на чужих адресах — браузер их не покажет (tsk-759)' }
    [pscustomobject]@{ Check = 'missing-attachments'; TaskName = 'LMS missing attachments weekly';         At = '09:30'; Default = $false; Why = 'задания с файловым условием без файла (tsk-369)' }
    # Сводка идёт последней и с запасом по времени: она читает итоги сегодняшних
    # прогонов, а самый долгий чек (stale-verdicts) занимает минуты. Аргумент у неё
    # другой — не имя чека, а флаг.
    [pscustomobject]@{ Check = 'digest';              TaskName = 'LMS weekly checks digest';               At = '09:45'; Default = $true;  Why = 'сводка оператору в Telegram, только при находках (tsk-778)'; Arguments = '--digest' }
)

if ($Only) {
    $plan = $plan | Where-Object { $_.Check -eq $Only }
} else {
    $plan = $plan | Where-Object { $_.Default -or ($WithMissingAttachments -and $_.Check -eq 'missing-attachments') }
}

if ($Uninstall) {
    foreach ($item in $plan) {
        if (Get-ScheduledTask -TaskName $item.TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $item.TaskName -Confirm:$false
            Write-Host "Снята задача '$($item.TaskName)'."
        } else {
            Write-Host "Задачи '$($item.TaskName)' и не было."
        }
    }
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo 'scripts\weekly_checks.py'
if (-not (Test-Path -LiteralPath $runner)) {
    Write-Error "Не найден $runner"
    exit 1
}

# Именно pythonw.exe. Системный интерпретатор не подойдёт — в нём нет зависимостей
# проекта, и его отсутствие должно быть внятной ошибкой сейчас, а не молчаливым
# провалом раз в неделю.
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Error "Не найден $pythonw — создайте venv проекта перед установкой задач."
    exit 1
}

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# StartWhenAvailable: пропущенный понедельник догоняется, иначе неделя молча выпадает
# — а «никто не заметил» и есть та беда, ради которой чеки заведены.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -Hidden

foreach ($item in $plan) {
    $argument = if ($item.PSObject.Properties['Arguments']) { $item.Arguments } else { $item.Check }
    $action = New-ScheduledTaskAction `
        -Execute $pythonw `
        -Argument "`"$runner`" $argument" `
        -WorkingDirectory $repo

    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $item.At

    Register-ScheduledTask `
        -TaskName $item.TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "tsk-641: еженедельный чек прода LMS — $($item.Why). Только чтение. Итог — logs\weekly_checks.log и журнал чека, окна не создаётся (pythonw). Код 0 = чек отработал (находки могли быть); ненулевой = чек не отработал (tsk-777)." `
        -Force | Out-Null

    Write-Host ("{0,-42} понедельник {1}  →  {2}" -f $item.TaskName, $item.At, $item.Check)
}

Write-Host ""
Write-Host "Проверить:  Get-ScheduledTask -TaskName 'LMS*' | Get-ScheduledTaskInfo"
Write-Host "Прогнать:   Start-ScheduledTask -TaskName '<имя задачи>'"
Write-Host "Журналы:    $(Join-Path $repo 'logs')"
