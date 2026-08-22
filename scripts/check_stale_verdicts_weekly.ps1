# Еженедельный чек: незачёты, которые нынешние правила заданий признали бы зачётами
# (tsk-636). Read-only: ни одной записи в БД. Пара к scripts\check_ungradable_tasks_weekly.ps1.
#
# Зачем: правка эталона не пересчитывает уже выставленные вердикты, и ученик остаётся
# с незачётом на верном ответе. Наружу это не всплывает — ни ошибки, ни лога. Находку
# 8 августа (10 работ) никто не заметил две недели, за это время добавилась ещё одна.
# Поэтому проверка стала регулярной, а не разовой.
#
# Что делать с находкой: сперва посмотреть журнал правок эталона —
#   SELECT changed_at, changed_by, old_answer_key, new_answer_key
#   FROM task_audit WHERE task_id = <id> AND new_answer_key IS NOT NULL ORDER BY changed_at;
# Есть правка ПОСЛЕ сдачи — вердикт был верным на тот момент, эталон был неполон.
# Правки нет — это уже вопрос к движку проверки, разбирать отдельно.
#
# Ручной прогон:  powershell -ExecutionPolicy Bypass -File scripts\check_stale_verdicts_weekly.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "stale_verdicts_check.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

function Write-Log([string]$text) {
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    Add-Content -Path $log -Value $text -Encoding UTF8
}

try {
    Set-Location $repo

    # DSN скрипт читает сам из .mcp.json (learn_prod_db) — проверять надо то, что видят
    # ученики, а в .env проекта лежит dev-база (tsk-246). Значение нигде не печатаем.
    $py = Join-Path $repo ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }

    $ErrorActionPreference = "Continue"
    $out = & $py (Join-Path $repo "scripts\audit_stale_false_verdicts_tsk602.py") --quiet 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"

    switch ($code) {
        0 {
            # Ноль — расхождений нет. Но скрипт мог напечатать смежные сигналы
            # (сменённый тип задания, непроверяемая работа) — их тоже в журнал.
            if ($out) {
                Write-Log "$stamp  Расхождений нет, но есть что посмотреть:"
                Write-Log ($out | Out-String)
            } else {
                Write-Log "$stamp  OK: устаревших незачётов нет"
            }
        }
        1 {
            Write-Log "$stamp  НАЙДЕНЫ устаревшие незачёты:"
            Write-Log ($out | Out-String)
            Write-Log "  Сперва журнал правок эталона (task_audit.new_answer_key), см. шапку файла."
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
