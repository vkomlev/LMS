<#
.SYNOPSIS
    Запустить pytest, предварительно убедившись, что локальный Redis поднят.

.DESCRIPTION
    tsk-611. Одна команда на вход вместо голого `pytest tests/`: сначала
    ensure-redis.ps1 (проверяет и при необходимости поднимает Redis), затем
    прогон из виртуального окружения проекта.

    Если Redis поднять не удалось — прогон всё равно идёт: тесты с маркером
    `requires_redis` честно пропускаются с причиной (см. tests/conftest.py), а
    не падают внутренностями драйвера. Предупреждение печатается явно, чтобы
    пропуски не приняли за полное покрытие.

.PARAMETER PytestArgs
    Всё, что нужно передать в pytest. По умолчанию — `tests/`.

.EXAMPLE
    .\scripts\dev\run-tests.ps1

.EXAMPLE
    .\scripts\dev\run-tests.ps1 tests/test_y5_guest_endpoints.py -q
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ensureRedis = Join-Path $PSScriptRoot 'ensure-redis.ps1'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ensureRedis
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Redis поднять не удалось. Прогон продолжится, но тесты, которым нужен Redis, будут ПРОПУЩЕНЫ — смотри строку "skipped" в итоге.'
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Warning "Виртуального окружения нет ($python), беру python из PATH."
    $python = 'python'
}

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) { $PytestArgs = @('tests/') }

Push-Location $projectRoot
try {
    & $python -m pytest @PytestArgs -ra
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
