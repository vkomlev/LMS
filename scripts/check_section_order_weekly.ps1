# Еженедельный чек: порядок разделов курсов в ПРОДЕ LMS (tsk-237).
# Запускается Windows Task Scheduler по понедельникам. Read-only: ни одной записи в БД.
#
# Зачем: order_number в course_parents ставится по мере публикации (новое ребро без
# явного номера -> триггер даёт max+1), поэтому раздел, опубликованный позже, встаёт
# в конец, и ученик видит разделы вразнобой. Ловилось только глазами оператора по
# скриншоту (Трек 1, Трек 3, чат-боты).
#
# Ручной прогон:  powershell -ExecutionPolicy Bypass -File scripts\check_section_order_weekly.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "section_order_check.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

function Write-Log([string]$text) {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    Add-Content -Path $log -Value $text -Encoding UTF8
}

# Всё тело — в try/catch: под планировщиком консоли нет, и упавший скрипт молча возвращал
# код 1 без единой строки в логе. Причина обязана попадать в лог, иначе чек не диагностируем.
try {
    # Рабочий каталог = корень репозитория. app/core/config.py создаёт uploads/* ОТНОСИТЕЛЬНО
    # cwd, а планировщик стартует в C:\Windows\System32 → mkdir там падает с WinError 5.
    Set-Location $repo

    # Прод-DSN берём из .mcp.json (learn_prod_db): в .env проекта лежит dev-база (tsk-246),
    # а проверять надо то, что видят ученики. Значение нигде не печатаем.
    $mcp = "D:\Work\CreateCourses\.mcp.json"
    $dsn = $null
    foreach ($a in ((Get-Content $mcp -Raw | ConvertFrom-Json).mcpServers.learn_prod_db.args)) {
        if ($a -like "postgresql://*") { $dsn = $a -replace "^postgresql://", "postgresql+asyncpg://"; break }
    }
    if (-not $dsn) {
        Write-Log "$stamp  ОШИБКА: прод-DSN не найден в $mcp"
        exit 2
    }

    $py = Join-Path $repo ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }

    $env:DATABASE_URL = $dsn
    try {
        # Внешняя программа пишет диагностику в stderr; при "Stop" это терминирующая ошибка
        # PowerShell, и код выхода питона до switch не доходит. Здесь stderr — просто текст.
        $ErrorActionPreference = "Continue"
        $out = & $py (Join-Path $repo "scripts\check_section_order.py") --quiet 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = "Stop"
        $env:DATABASE_URL = $null
    }

    switch ($code) {
        0 { Write-Log "$stamp  OK: порядок разделов верный" }
        1 {
            Write-Log "$stamp  НАРУШЕН порядок разделов:"
            Write-Log ($out | Out-String)
            Write-Log "  Как чинить — tsk-237 в трекере."
        }
        default {
            Write-Log "$stamp  ОШИБКА чека (код $code):"
            Write-Log ($out | Out-String)
        }
    }

    exit $code
} catch {
    Write-Log "$stamp  ОШИБКА обёртки: $_"
    exit 2
}
