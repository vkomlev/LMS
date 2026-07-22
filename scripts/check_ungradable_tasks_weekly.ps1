# Еженедельный чек: непроверяемые задания в ПРОДЕ LMS (tsk-361).
# Read-only: ни одной записи в БД. Пара к scripts\check_section_order_weekly.ps1.
#
# Зачем: задание без правила проверки (или с пустым правилом) молча становится
# «всегда неверно» — ни ошибки, ни лога. Три разбора подряд (tsk-325, tsk-100,
# tsk-361) находили такие задания только вручную, поэтому чек стал регулярным.
#
# Ручной прогон:  powershell -ExecutionPolicy Bypass -File scripts\check_ungradable_tasks_weekly.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "ungradable_tasks_check.log"
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
        $out = & $py (Join-Path $repo "scripts\check_ungradable_tasks.py") --quiet 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = "Stop"
        $env:DATABASE_URL = $null
    }

    switch ($code) {
        0 { Write-Log "$stamp  OK: непроверяемых заданий нет" }
        1 {
            Write-Log "$stamp  НАЙДЕНЫ непроверяемые задания:"
            Write-Log ($out | Out-String)
            Write-Log "  Как чинить — tsk-361 в трекере."
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
