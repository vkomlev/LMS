# Smoke: комментарий в ответах SA_COM
# Отправить ответ с comment -> завершить попытку -> GET попытку -> проверить, что comment вернулся.
# Проверка в БД: task_results.answer_json->'response'->>'comment' (через MCP или SQL).
# Требует: $env:API_KEY, $env:TASK_ID (id задачи SA_COM). Опционально $env:HOST, $env:USER_ID, $env:COURSE_ID.

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$hostUrl = if ($env:HOST) { $env:HOST } else { "http://localhost:8000" }
$apiKey = $env:API_KEY
$taskId = $env:TASK_ID
$userId = if ($env:USER_ID) { $env:USER_ID } else { "1" }
$courseId = if ($env:COURSE_ID) { $env:COURSE_ID } else { "1" }

if (-not $apiKey -or -not $taskId) {
    Write-Host "Задайте API_KEY и TASK_ID (id задачи SA_COM). Пример: `$env:API_KEY='bot-key-1'; `$env:TASK_ID=30; .\scripts\smoke_attempts_answers_comment.ps1"
    exit 1
}

$q = "?api_key=$apiKey"
$commentText = "Smoke comment from script"

# 1) Создать попытку
try {
    $bodyCreate = '{"user_id":' + $userId + ',"course_id":' + $courseId + ',"source_system":"smoke"}'
    $r = Invoke-WebRequest -Uri "$hostUrl/api/v1/attempts$q" -Method POST -ContentType "application/json" -Body $bodyCreate -UseBasicParsing
    $attemptId = ($r.Content | ConvertFrom-Json).id
} catch {
    Write-Host "[FAIL] Create attempt: $($_.Exception.Message)"
    exit 1
}
Write-Host "[INFO] Attempt id: $attemptId"

# 2) Отправить ответ с comment
$bodyAnswers = '{"items":[{"task_id":' + $taskId + ',"answer":{"type":"SA_COM","response":{"value":"42","comment":"' + $commentText + '"}}}]}'
try {
    $r = Invoke-WebRequest -Uri "$hostUrl/api/v1/attempts/$attemptId/answers$q" -Method POST -ContentType "application/json" -Body $bodyAnswers -UseBasicParsing
    if ($r.StatusCode -ne 200) { Write-Host "[FAIL] POST answers: $($r.StatusCode)"; exit 1 }
} catch {
    Write-Host "[FAIL] POST answers: $($_.Exception.Message)"
    exit 1
}
Write-Host "[PASS] POST answers with comment -> 200"

# 3) Завершить попытку
try {
    $r = Invoke-WebRequest -Uri "$hostUrl/api/v1/attempts/$attemptId/finish$q" -Method POST -UseBasicParsing
    if ($r.StatusCode -ne 200) { Write-Host "[FAIL] POST finish: $($r.StatusCode)"; exit 1 }
} catch {
    Write-Host "[FAIL] POST finish: $($_.Exception.Message)"
    exit 1
}
Write-Host "[PASS] POST finish -> 200"

# 4) GET попытку и проверить comment в results
try {
    $r = Invoke-WebRequest -Uri "$hostUrl/api/v1/attempts/$attemptId$q" -Method GET -UseBasicParsing
    $json = $r.Content | ConvertFrom-Json
    $result = $json.results | Where-Object { $_.task_id -eq [int]$taskId } | Select-Object -First 1
    $comment = $result.answer_json.response.comment
    if ($comment -ne $commentText) {
        Write-Host "[FAIL] GET attempt: expected comment '$commentText', got '$comment'"
        exit 1
    }
} catch {
    Write-Host "[FAIL] GET attempt: $($_.Exception.Message)"
    exit 1
}
Write-Host "[PASS] GET attempt: answer_json.response.comment = '$comment'"

Write-Host "Smoke comment: OK. DB check: task_results answer_json.response.comment attempt_id=$attemptId task_id=$taskId"
