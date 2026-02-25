# Smoke: Learning Engine V1, этап 7 — сквозной маршрут (API)
# Параметры через env: API_KEY, USER_ID, COURSE_ID, TASK_ID; опционально: HOST, MATERIAL_ID, UPDATED_BY
# Использование: $env:API_KEY='key'; $env:USER_ID=1; $env:COURSE_ID=1; $env:TASK_ID=1; .\scripts\smoke_learning_engine_stage7.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$HostUrl = if ($env:HOST) { $env:HOST } else { "http://localhost:8000" }
$Base = "$HostUrl/api/v1"
$ApiKey = $env:API_KEY
$StudentId = $env:USER_ID
$CourseId = $env:COURSE_ID
$TaskId = $env:TASK_ID
$MaterialId = $env:MATERIAL_ID
$UpdatedBy = $env:UPDATED_BY
$query = if ($ApiKey) { "?api_key=$ApiKey" } else { "" }

if (-not $ApiKey -or -not $StudentId -or -not $CourseId -or -not $TaskId) {
    Write-Host "Задайте API_KEY, USER_ID, COURSE_ID, TASK_ID. Опционально: HOST, MATERIAL_ID, UPDATED_BY"
    Write-Host "Пример: `$env:API_KEY='key'; `$env:USER_ID=1; `$env:COURSE_ID=1; `$env:TASK_ID=1; .\scripts\smoke_learning_engine_stage7.ps1"
    exit 1
}

$script:AttemptId = $null
$allOk = $true

function Invoke-Get {
    param([string]$Uri)
    try {
        $r = Invoke-RestMethod -Uri $Uri -Method Get
        return @($true, $r)
    } catch {
        return @($false, $_.Exception.Message)
    }
}

function Invoke-Post {
    param([string]$Uri, [object]$Body = $null)
    try {
        $bodyJson = if ($Body) { $Body | ConvertTo-Json -Depth 5 } else { "{}" }
        $r = Invoke-RestMethod -Uri $Uri -Method Post -ContentType "application/json" -Body $bodyJson
        return @($true, $r)
    } catch {
        return @($false, $_.Exception.Message)
    }
}

function Step {
    param([string]$Name, [scriptblock]$Run)
    Write-Host "--- $Name ---"
    $ok, $result = & $Run
    if ($ok) {
        Write-Host "[PASS] $Name"
        return $result
    }
    Write-Host "[FAIL] $Name : $result"
    $script:allOk = $false
    return $null
}

function StepSkip {
    param([string]$Name, [string]$Reason)
    Write-Host "--- $Name ---"
    Write-Host "[SKIP] $Name ($Reason)"
    return $null
}

# 1. next-item
$next = Step -Name "GET /learning/next-item" -Run {
    Invoke-Get -Uri "$Base/learning/next-item?student_id=$StudentId$query"
}
if (-not $next) { Write-Host "Продолжаем без next-item результата." }

# 2. materials/complete (optional)
if ($MaterialId) {
    $null = Step -Name "POST /learning/materials/$MaterialId/complete" -Run {
        Invoke-Post -Uri "$Base/learning/materials/$MaterialId/complete$query" -Body @{ student_id = $StudentId }
    }
} else {
    StepSkip -Name "POST /learning/materials/.../complete" -Reason "MATERIAL_ID не задан"
}

# 3. start-or-get-attempt
$attemptResp = Step -Name "POST /learning/tasks/$TaskId/start-or-get-attempt" -Run {
    Invoke-Post -Uri "$Base/learning/tasks/$TaskId/start-or-get-attempt$query" -Body @{ student_id = $StudentId; source_system = "learning_api" }
}
if ($attemptResp -and $attemptResp.attempt_id) {
    $script:AttemptId = $attemptResp.attempt_id
}

# 4. GET attempt (extended fields)
if ($script:AttemptId) {
    $null = Step -Name "GET /attempts/$($script:AttemptId)" -Run {
        Invoke-Get -Uri "$Base/attempts/$($script:AttemptId)$query"
    }
} else {
    StepSkip -Name "GET /attempts/{id}" -Reason "attempt_id не получен"
}

# 5. GET task + list (hints)
$null = Step -Name "GET /tasks/$TaskId (hints)" -Run {
    $t = Invoke-Get -Uri "$Base/tasks/$TaskId$query"
    if ($t[0] -and $t[1].hints_text -ne $null -and $t[1].has_hints -ne $null) { return @($true, $t[1]) }
    return @($false, "нет полей hints_text/has_hints в ответе")
}
$null = Step -Name "GET /tasks/ list (hints)" -Run {
    $r = Invoke-Get -Uri "$Base/tasks/?limit=2$query"
    if ($r[0] -and $r[1].items -and $r[1].items[0].hints_text -ne $null) { return @($true, $r[1]) }
    if ($r[0] -and $r[1].items.Count -eq 0) { return @($true, $r[1]) }
    return @($false, "нет поля hints в элементах")
}

# 6. stats by-user, by-course, by-task
$null = Step -Name "GET /task-results/stats/by-user/$StudentId" -Run {
    $s = Invoke-Get -Uri "$Base/task-results/stats/by-user/$StudentId$query"
    if ($s[0] -and $s[1].progress_percent -ne $null -and $s[1].passed_tasks_count -ne $null) { return @($true, $s[1]) }
    return @($false, "нет last-based полей")
}
$null = Step -Name "GET /task-results/stats/by-course/$CourseId" -Run {
    $s = Invoke-Get -Uri "$Base/task-results/stats/by-course/$CourseId$query"
    if ($s[0] -and $s[1].progress_percent -ne $null) { return @($true, $s[1]) }
    return @($false, "нет last-based полей")
}
$null = Step -Name "GET /task-results/stats/by-task/$TaskId" -Run {
    $s = Invoke-Get -Uri "$Base/task-results/stats/by-task/$TaskId$query"
    if ($s[0] -and $s[1].last_passed_count -ne $null) { return @($true, $s[1]) }
    return @($false, "нет last-based полей")
}

# 7. request-help
$null = Step -Name "POST /learning/tasks/$TaskId/request-help" -Run {
    Invoke-Post -Uri "$Base/learning/tasks/$TaskId/request-help$query" -Body @{ student_id = $StudentId; message = "Smoke stage7" }
}

# 8. teacher/task-limits/override
if ($UpdatedBy) {
    $null = Step -Name "POST /teacher/task-limits/override" -Run {
        Invoke-Post -Uri "$Base/teacher/task-limits/override$query" -Body @{ student_id = $StudentId; task_id = $TaskId; max_attempts_override = 5; updated_by = [int]$UpdatedBy }
    }
} else {
    StepSkip -Name "POST /teacher/task-limits/override" -Reason "UPDATED_BY не задан"
}

Write-Host ""
if ($script:allOk) {
    Write-Host "Smoke этапа 7: все проверки пройдены."
    exit 0
}
Write-Host "Smoke этапа 7: есть провалы."
exit 1
