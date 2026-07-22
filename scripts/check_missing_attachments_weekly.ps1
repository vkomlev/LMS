# Еженедельный чек: задания, которые требуют файл-приложение, а файла нет (tsk-369).
# Read-only: ни одной записи в БД. Пара к scripts\check_ungradable_tasks_weekly.ps1.
#
# Зачем: у такого задания есть и текст, и правило проверки — формально оно исправно,
# а решить его нельзя: данные лежали в файле, которого нет. Класс не ловился ничем,
# нашёлся только сплошным разбором (tsk-369, 224 задания на проде).
#
# Ручной прогон:  powershell -ExecutionPolicy Bypass -File scripts\check_missing_attachments_weekly.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "missing_attachments_check.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

function Write-Log([string]$text) {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    Add-Content -Path $log -Value $text -Encoding UTF8
}

try {
    Set-Location $repo

    # Прод-DSN берём из .mcp.json (learn_prod_db): в .env проекта лежит dev-база (tsk-246),
    # а проверять надо то, что видят ученики. Значение нигде не печатаем.
    $mcp = "D:\Work\LMS\.mcp.json"
    $dsn = $null
    foreach ($a in ((Get-Content $mcp -Raw | ConvertFrom-Json).mcpServers.learn_prod_db.args)) {
        if ($a -like "postgresql://*") { $dsn = $a; break }
    }
    if (-not $dsn) {
        Write-Log "$stamp  ОШИБКА: прод-DSN не найден в $mcp"
        exit 2
    }

    $py = Join-Path $repo ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }

    $env:DATABASE_URL = $dsn
    try {
        $ErrorActionPreference = "Continue"
        $out = & $py (Join-Path $repo "scripts\check_missing_attachments.py") --quiet 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = "Stop"
        $env:DATABASE_URL = $null
    }

    switch ($code) {
        0 { Write-Log "$stamp  OK: у всех заданий с файловым условием файл на месте" }
        1 {
            Write-Log "$stamp  НАЙДЕНЫ задания без файла-приложения:"
            Write-Log ($out | Out-String)
            Write-Log "  Как чинить — tsk-369 в трекере (скрипты scripts	sk369_*.py)."
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
