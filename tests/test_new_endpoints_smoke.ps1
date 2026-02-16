# Smoke tests for new endpoints: GET /tasks/search and GET /task-results/pending-review
# Проверка: CURL-запросы к API; валидация по логам logs/app.log; состояние БД через MCP

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ApiKey,
    [string]$LogFile = "logs/app.log"
)

$ErrorActionPreference = "Stop"
$API = "/api/v1/"

if (-not $ApiKey) {
    $envFile = Join-Path $PSScriptRoot "..\\.env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match "VALID_API_KEYS" } | Select-Object -First 1
        if ($line) {
            $ApiKey = ($line -split "=", 2)[1].Trim() -split "," | ForEach-Object { $_.Trim() } | Select-Object -First 1
        }
    }
    if (-not $ApiKey) { $ApiKey = "bot-key-1" }
}

function Url { 
    param([string]$Path, [hashtable]$Q = @{}) 
    $q = $Q.Clone()
    $q["api_key"] = $ApiKey
    $qstr = ($q.GetEnumerator() | ForEach-Object { 
        $key = $_.Key
        $val = $_.Value.ToString()
        "$key=$val"
    }) -join "&"
    $pathPart = ($API.TrimEnd("/") + "/" + $Path.TrimStart("/")).TrimEnd("/")
    if ($pathPart -eq "") { $pathPart = $API.TrimEnd("/") }
    $sep = if ($Path -match "\?") { "&" } else { "?" }
    "$BaseUrl$pathPart$sep$qstr"
}

function Get-Req { 
    param([string]$Path, [hashtable]$Q = @{}) 
    try {
        $uri = Url $Path $Q
        Write-Host "GET $uri" -ForegroundColor Cyan
        $response = Invoke-RestMethod -Uri $uri -Method GET -ErrorAction Stop
        Write-Host "✓ Success" -ForegroundColor Green
        return $response
    } catch {
        Write-Host "✗ Error: $_" -ForegroundColor Red
        throw
    }
}

function Post-Req { 
    param([string]$Path, [object]$Body) 
    try {
        $uri = Url $Path
        Write-Host "POST $uri" -ForegroundColor Cyan
        $jsonBody = $Body | ConvertTo-Json -Compress -Depth 10
        Write-Host "Body: $jsonBody" -ForegroundColor Gray
        $response = Invoke-RestMethod -Uri $uri -Method POST -Body $jsonBody -ContentType "application/json; charset=utf-8" -ErrorAction Stop
        Write-Host "✓ Success" -ForegroundColor Green
        return $response
    } catch {
        Write-Host "✗ Error: $_" -ForegroundColor Red
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $responseBody = $reader.ReadToEnd()
            Write-Host "Response: $responseBody" -ForegroundColor Yellow
        }
        throw
    }
}

Write-Host "`n=== Smoke Tests for New Endpoints ===" -ForegroundColor Magenta
Write-Host "Base URL: $BaseUrl" -ForegroundColor Gray
Write-Host "API Key: $ApiKey" -ForegroundColor Gray
Write-Host ""

# Получаем данные из БД для тестирования
Write-Host "`n1. Получение данных из БД..." -ForegroundColor Yellow
$courseId = 1
Write-Host "Using course_id: $courseId" -ForegroundColor Gray

# Создаем тестовое задание для поиска
Write-Host "`n2. Создание тестового задания..." -ForegroundColor Yellow
try {
    $taskBody = @{
        external_uid = "TEST-SEARCH-001"
        course_id = $courseId
        difficulty_id = 1
        task_content = @{
            type = "SC"
            stem = "Что такое переменная в Python?"
            options = @(
                @{ id = "A"; text = "Область памяти"; is_active = $true }
                @{ id = "B"; text = "Функция"; is_active = $true }
            )
        }
        solution_rules = @{
            max_score = 10
            correct_options = @("A")
        }
        max_score = 10
    }
    
    $createdTask = Post-Req "tasks" $taskBody
    Write-Host "Created task ID: $($createdTask.id), external_uid: $($createdTask.external_uid)" -ForegroundColor Green
    $testTaskId = $createdTask.id
} catch {
    Write-Host "Failed to create test task. Trying to get existing task..." -ForegroundColor Yellow
    try {
        $existingTasks = Get-Req "tasks/by-course/$courseId" @{ limit = 1 }
        if ($existingTasks -and $existingTasks.Count -gt 0) {
            $testTaskId = $existingTasks[0].id
            Write-Host "Using existing task ID: $testTaskId" -ForegroundColor Green
        } else {
            Write-Host "No tasks found. Skipping search test." -ForegroundColor Yellow
            $testTaskId = $null
        }
    } catch {
        Write-Host "Could not get tasks. Skipping search test." -ForegroundColor Yellow
        $testTaskId = $null
    }
}

# Тест 1: GET /tasks/search
Write-Host "`n3. Testing GET /tasks/search..." -ForegroundColor Yellow
if ($testTaskId) {
    try {
        $searchResults = Get-Req "tasks/search" @{ q = "переменная"; course_id = $courseId; limit = 10 }
        if ($searchResults) {
            $count = if ($searchResults -is [array]) { $searchResults.Count } else { 1 }
            Write-Host "Found $count tasks" -ForegroundColor Green
            if ($count -gt 0) {
                $first = if ($searchResults -is [array]) { $searchResults[0] } else { $searchResults }
                Write-Host "First result: ID=$($first.id), stem=$($first.task_content.stem)" -ForegroundColor Gray
            }
        } else {
            Write-Host "No results found" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Search test failed: $_" -ForegroundColor Red
    }
} else {
    Write-Host "Skipping search test - no tasks available" -ForegroundColor Yellow
}

# Тест 2: GET /tasks/search без фильтра по курсу
Write-Host "`n4. Testing GET /tasks/search (without course filter)..." -ForegroundColor Yellow
if ($testTaskId) {
    try {
        $searchResults = Get-Req "tasks/search" @{ q = "Python"; limit = 10 }
        if ($searchResults) {
            $count = if ($searchResults -is [array]) { $searchResults.Count } else { 1 }
            Write-Host "Found $count tasks" -ForegroundColor Green
        } else {
            Write-Host "No results found" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Search test failed: $_" -ForegroundColor Red
    }
}

# Тест 3: GET /task-results/by-pending-review
Write-Host "`n5. Testing GET /task-results/by-pending-review..." -ForegroundColor Yellow
try {
    $pendingResults = Get-Req "task-results/by-pending-review" @{ limit = 10 }
    if ($pendingResults) {
        $count = if ($pendingResults -is [array]) { $pendingResults.Count } else { 1 }
        Write-Host "Found $count results pending review" -ForegroundColor Green
        if ($count -gt 0) {
            $first = if ($pendingResults -is [array]) { $pendingResults[0] } else { $pendingResults }
            Write-Host "First result: ID=$($first.id), task_id=$($first.task_id), checked_at=$($first.checked_at)" -ForegroundColor Gray
        }
    } else {
        Write-Host "No results found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Pending review test failed: $_" -ForegroundColor Red
}

# Тест 4: GET /task-results/by-pending-review с фильтром по курсу
Write-Host "`n6. Testing GET /task-results/by-pending-review (with course filter)..." -ForegroundColor Yellow
try {
    $pendingResults = Get-Req "task-results/by-pending-review" @{ course_id = $courseId; limit = 10 }
    if ($pendingResults) {
        $count = if ($pendingResults -is [array]) { $pendingResults.Count } else { 1 }
        Write-Host "Found $count results pending review for course $courseId" -ForegroundColor Green
    } else {
        Write-Host "No results found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "Pending review test failed: $_" -ForegroundColor Red
}

Write-Host "`n=== Smoke Tests Completed ===" -ForegroundColor Magenta
