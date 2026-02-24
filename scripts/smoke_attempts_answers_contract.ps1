# Smoke-проверка контракта POST /attempts/{id}/answers (SA_COM / SA+COM)
# Использование: задайте $env:HOST, $env:ATTEMPT_ID, $env:TASK_ID, $env:API_KEY при необходимости и запустите скрипт.
# Ожидания: SA_COM+value -> 200, SA+COM+value -> 200, неверный type -> 422.

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$hostUrl = if ($env:HOST) { $env:HOST } else { "http://localhost:8000" }
$attemptId = $env:ATTEMPT_ID
$taskId = $env:TASK_ID
$apiKey = $env:API_KEY
$query = if ($apiKey) { "?api_key=$apiKey" } else { "" }

if (-not $attemptId -or -not $taskId) {
    Write-Host "Задайте ATTEMPT_ID и TASK_ID (незавершенная попытка и задача типа SA_COM). Пример: `$env:ATTEMPT_ID=1; `$env:TASK_ID=5; .\scripts\smoke_attempts_answers_contract.ps1"
    exit 1
}

$baseUrl = "$hostUrl/api/v1/attempts/$attemptId/answers$query"

function Invoke-Check {
    param([string]$Name, [string]$Body, [int]$ExpectedCode)
    try {
        $r = Invoke-WebRequest -Uri $baseUrl -Method POST -ContentType "application/json" -Body $Body -UseBasicParsing
        $code = [int]$r.StatusCode
    } catch {
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode.value__
            $r = $null
        } else {
            Write-Host "[FAIL] $Name -> исключение: $($_.Exception.Message)"
            return $false
        }
    }
    if ($code -eq $ExpectedCode) {
        Write-Host "[PASS] $Name -> $code"
        return $true
    }
    $content = if ($r) { $r.Content } else { "(тело не получено)" }
    Write-Host "[FAIL] $Name -> ожидался $ExpectedCode, получен $code. Body: $content"
    return $false
}

$ok = $true
$ok = (Invoke-Check -Name "SA_COM + response.value" -Body '{"items":[{"task_id":' + $taskId + ',"answer":{"type":"SA_COM","response":{"value":"42"}}}]}' -ExpectedCode 200) -and $ok
$ok = (Invoke-Check -Name "SA+COM alias + response.value" -Body '{"items":[{"task_id":' + $taskId + ',"answer":{"type":"SA+COM","response":{"value":"42"}}}]}' -ExpectedCode 200) -and $ok
$ok = (Invoke-Check -Name "Invalid type (SA_PLUS_COM)" -Body '{"items":[{"task_id":' + $taskId + ',"answer":{"type":"SA_PLUS_COM","response":{"value":"42"}}}]}' -ExpectedCode 422) -and $ok

if ($ok) { Write-Host "Все проверки контракта пройдены."; exit 0 }
exit 1
