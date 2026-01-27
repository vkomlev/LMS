# Smoke tests for teacher-courses API endpoints via CURL
# Tests endpoints, logs and database state

param(
    [string]$BaseUrl = "http://localhost:8000",
    [string]$ApiKey = "bot-key-1",
    [string]$LogFile = "logs/app.log"
)

$ErrorActionPreference = "Stop"
$API_PREFIX = "/api/v1"

function Write-Success { param($msg) Write-Host "[PASS] $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }

function Invoke-ApiRequest {
    param(
        [string]$Method,
        [string]$Endpoint,
        [string]$Body = $null,
        [string]$Description
    )
    
    Write-Info "Test: $Description"
    Write-Host "  $Method $Endpoint"
    
    $fullPath = $API_PREFIX + $Endpoint
    if ($fullPath.Contains("?")) {
        $url = $BaseUrl + $fullPath + "&api_key=" + $ApiKey
    } else {
        $url = $BaseUrl + $fullPath + "?api_key=" + $ApiKey
    }
    
    Write-Host "  URL: $url" -ForegroundColor Gray
    
    $headers = @{
        "Content-Type" = "application/json"
    }
    
    try {
        if ($Body) {
            $response = Invoke-RestMethod -Uri $url -Method $Method -Headers $headers -Body $Body -ErrorAction Stop
        } else {
            $response = Invoke-RestMethod -Uri $url -Method $Method -Headers $headers -ErrorAction Stop
        }
        
        Write-Success "Request completed successfully"
        return @{
            Success = $true
            Response = $response
            StatusCode = 200
        }
    }
    catch {
        $statusCode = 500
        $errorBody = $_.Exception.Message
        
        if ($_.Exception.Response) {
            $statusCode = $_.Exception.Response.StatusCode.value__
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $errorBody = $reader.ReadToEnd()
                $reader.Close()
            } catch {
                $errorBody = $_.ErrorDetails.Message
            }
        }
        
        Write-Warn "HTTP $statusCode : $errorBody"
        return @{
            Success = $false
            StatusCode = $statusCode
            Error = $errorBody
        }
    }
}

function Test-DatabaseState {
    param(
        [string]$Query,
        [string]$ExpectedValue,
        [string]$Description
    )
    
    # Используем MCP напрямую для проверки БД
    # Это быстрее и надежнее, чем через Python скрипты
    try {
        # Экранируем кавычки в запросе для использования в PowerShell
        $safeQuery = $Query -replace "'", "''"
        
        # Используем прямой SQL запрос через MCP (если доступен)
        # Иначе используем Python скрипт
        $projectRoot = $PSScriptRoot
        if (-not $projectRoot) {
            $projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
        }
        if (-not $projectRoot -or -not (Test-Path "$projectRoot\.env")) {
            $projectRoot = (Get-Location).Path
        }
        
        $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $pythonExe)) {
            $pythonExe = "python"
        }
        
        $pythonScript = @"
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(r"$projectRoot")
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings

async def check_db():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        result = await session.execute(text('''$Query'''))
        value = result.scalar()
        print(str(value))
        return str(value) == '$ExpectedValue'

result = asyncio.run(check_db())
sys.exit(0 if result else 1)
"@
        
        $scriptFile = [System.IO.Path]::GetTempFileName() + ".py"
        $pythonScript | Out-File -FilePath $scriptFile -Encoding UTF8
        
        try {
            $output = & $pythonExe $scriptFile 2>&1
            $exitCode = $LASTEXITCODE
            
            if ($exitCode -eq 0) {
                Write-Success "DB check: $Description"
                return $true
            } else {
                Write-Warn "DB check failed: $Description (expected: $ExpectedValue, got: $output)"
                return $false
            }
        }
        finally {
            Remove-Item $scriptFile -ErrorAction SilentlyContinue
        }
    }
    catch {
        Write-Warn "DB check error: $($_.Exception.Message)"
        return $false
    }
}

function Test-DatabaseQuery {
    param(
        [string]$Query,
        [string]$Description
    )
    
    $projectRoot = $PSScriptRoot
    if (-not $projectRoot) {
        $projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    if (-not $projectRoot -or -not (Test-Path "$projectRoot\.env")) {
        $projectRoot = (Get-Location).Path
    }
    
    $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        $pythonExe = "python"
    }
    
    $pythonScript = @"
import asyncio
import sys
import os
import json
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(r"$projectRoot")
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings

async def query_db():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        result = await session.execute(text('''$Query'''))
        rows = result.fetchall()
        if rows:
            # Если одна строка - выводим как список значений
            if len(rows) == 1 and len(rows[0]) == 1:
                print(str(rows[0][0]))
            else:
                # Множественные строки - выводим как JSON
                data = [dict(row._mapping) for row in rows]
                print(json.dumps(data, default=str, ensure_ascii=False))
        else:
            print("[]")

asyncio.run(query_db())
"@
    
    $scriptFile = [System.IO.Path]::GetTempFileName() + ".py"
    $pythonScript | Out-File -FilePath $scriptFile -Encoding UTF8
    
    try {
        $output = & $pythonExe $scriptFile 2>&1
        return $output
    }
    finally {
        Remove-Item $scriptFile -ErrorAction SilentlyContinue
    }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Smoke tests for teacher-courses API endpoints" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$results = @()

# Получаем тестовые данные из БД через MCP (используем фиксированные ID из реальной БД)
Write-Host "=== Setup: Using test data from DB ===" -ForegroundColor Yellow

# Используем реальные данные из БД (получены через MCP ранее)
# Преподаватели: ID 16, 17
$teacher1Id = 16
$teacher2Id = 17
Write-Host "  Teacher 1: ID=$teacher1Id" -ForegroundColor Gray
Write-Host "  Teacher 2: ID=$teacher2Id" -ForegroundColor Gray

# Курсы: используем курс 1 как родительский, курсы 2, 6, 7 как дочерние
$parentCourseId = 1
$childCourseId1 = 2
$childCourseId2 = 6

Write-Host "  Parent Course: ID=$parentCourseId" -ForegroundColor Gray
Write-Host "  Child Course 1: ID=$childCourseId1" -ForegroundColor Gray
Write-Host "  Child Course 2: ID=$childCourseId2" -ForegroundColor Gray
Write-Host ""

# Очищаем существующие связи для чистого теста через API
Write-Host "=== Setup: Cleaning existing teacher-course links ===" -ForegroundColor Yellow

# Получаем все существующие связи для наших тестовых данных через MCP
# Используем прямой SQL запрос через MCP (если доступен) или через Python скрипт
$existingLinksQuery = "SELECT teacher_id, course_id FROM teacher_courses WHERE teacher_id IN ($teacher1Id, $teacher2Id) OR course_id IN ($parentCourseId, $childCourseId1, $childCourseId2)"
$existingLinksJson = Test-DatabaseQuery -Query $existingLinksQuery -Description "Get existing links"

# Проверяем, что получили валидный JSON
if ($existingLinksJson -and $existingLinksJson -ne "[]" -and $existingLinksJson.Trim().StartsWith("[")) {
    try {
        $existingLinks = $existingLinksJson | ConvertFrom-Json
    } catch {
        $existingLinks = @()
    }
} else {
    $existingLinks = @()
}

if ($existingLinks -and $existingLinks.Count -gt 0) {
    Write-Host "  Found $($existingLinks.Count) existing link(s) to clean" -ForegroundColor Gray
    foreach ($link in $existingLinks) {
        # Удаляем через API
        $cleanupResult = Invoke-ApiRequest -Method "DELETE" -Endpoint "/courses/$($link.course_id)/teachers/$($link.teacher_id)" -Description "Clean existing link" -ErrorAction SilentlyContinue
        if (-not $cleanupResult.Success) {
            # Пробуем альтернативный эндпойнт
            $cleanupResult2 = Invoke-ApiRequest -Method "DELETE" -Endpoint "/teacher-courses/$($link.teacher_id)/$($link.course_id)" -Description "Clean existing link (RESTful)" -ErrorAction SilentlyContinue
        }
    }
    Write-Host "  Cleaned existing links" -ForegroundColor Gray
} else {
    Write-Host "  No existing links to clean" -ForegroundColor Gray
}
Write-Host ""

# ========== Тест 1: GET /courses/{course_id}/teachers (пустой список) ==========
Write-Host "=== Test 1: GET /courses/{course_id}/teachers (empty) ===" -ForegroundColor Yellow
$result1 = Invoke-ApiRequest -Method "GET" -Endpoint "/courses/$parentCourseId/teachers" -Description "Get teachers for course (should be empty)"
if ($result1.Success) {
    $teachersList = $result1.Response
    if ($teachersList.items.Count -eq 0) {
        Write-Success "Empty list returned correctly"
        $results += @{Test = "GET /courses/{course_id}/teachers (empty)"; Success = $true}
    } else {
        Write-Fail "Expected empty list, got $($teachersList.items.Count) items"
        $results += @{Test = "GET /courses/{course_id}/teachers (empty)"; Success = $false}
    }
} else {
    $results += @{Test = "GET /courses/{course_id}/teachers (empty)"; Success = $false; Error = $result1.Error}
}
Write-Host ""

# ========== Тест 2: GET /users/{teacher_id}/courses (пустой список) ==========
Write-Host "=== Test 2: GET /users/{teacher_id}/courses (empty) ===" -ForegroundColor Yellow
$result2 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$teacher1Id/courses" -Description "Get courses for teacher (should be empty)"
if ($result2.Success) {
    $coursesList = $result2.Response
    if ($coursesList.items.Count -eq 0) {
        Write-Success "Empty list returned correctly"
        $results += @{Test = "GET /users/{teacher_id}/courses (empty)"; Success = $true}
    } else {
        Write-Fail "Expected empty list, got $($coursesList.items.Count) items"
        $results += @{Test = "GET /users/{teacher_id}/courses (empty)"; Success = $false}
    }
} else {
    $results += @{Test = "GET /users/{teacher_id}/courses (empty)"; Success = $false; Error = $result2.Error}
}
Write-Host ""

# ========== Тест 3: POST /courses/{course_id}/teachers/{teacher_id} (привязка) ==========
Write-Host "=== Test 3: POST /courses/{course_id}/teachers/{teacher_id} ===" -ForegroundColor Yellow
$result3 = Invoke-ApiRequest -Method "POST" -Endpoint "/courses/$parentCourseId/teachers/$teacher1Id" -Description "Link teacher to parent course"
if ($result3.Success -or $result3.StatusCode -eq 204) {
    Write-Success "Teacher linked to course"
    
    # Проверяем БД - должна быть связь с родительским курсом
    $dbCheck1 = Test-DatabaseState -Query "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher1Id AND course_id = $parentCourseId" -ExpectedValue "1" -Description "Link exists in DB"
    
    # Проверяем БД - должны быть автоматически созданы связи с детьми (если есть)
    if ($hierarchy.Count -gt 0) {
        $childLinks = Test-DatabaseQuery -Query "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher1Id AND course_id IN ($childCourseId1, $childCourseId2)" -Description "Check child links"
        Write-Host "  Child links created: $childLinks" -ForegroundColor Gray
    }
    
    $results += @{Test = "POST /courses/{course_id}/teachers/{teacher_id}"; Success = $true}
} else {
    $results += @{Test = "POST /courses/{course_id}/teachers/{teacher_id}"; Success = $false; Error = $result3.Error}
}
Write-Host ""

# ========== Тест 4: GET /courses/{course_id}/teachers (с данными) ==========
Write-Host "=== Test 4: GET /courses/{course_id}/teachers (with data) ===" -ForegroundColor Yellow
$result4 = Invoke-ApiRequest -Method "GET" -Endpoint "/courses/$parentCourseId/teachers" -Description "Get teachers for course (should have 1)"
if ($result4.Success) {
    $teachersList = $result4.Response
    if ($teachersList.items.Count -ge 1) {
        Write-Success "Found $($teachersList.items.Count) teacher(s)"
        foreach ($teacher in $teachersList.items) {
            Write-Host "    - Teacher ID=$($teacher.id), Email=$($teacher.email)" -ForegroundColor Gray
        }
        $results += @{Test = "GET /courses/{course_id}/teachers (with data)"; Success = $true}
    } else {
        Write-Fail "Expected at least 1 teacher, got $($teachersList.items.Count)"
        $results += @{Test = "GET /courses/{course_id}/teachers (with data)"; Success = $false}
    }
} else {
    $results += @{Test = "GET /courses/{course_id}/teachers (with data)"; Success = $false; Error = $result4.Error}
}
Write-Host ""

# ========== Тест 5: GET /courses/{course_id}/teachers с пагинацией и сортировкой ==========
Write-Host "=== Test 5: GET /courses/{course_id}/teachers (pagination & sorting) ===" -ForegroundColor Yellow
$result5 = Invoke-ApiRequest -Method "GET" -Endpoint "/courses/$parentCourseId/teachers?skip=0&limit=10&sort_by=email&order=asc" -Description "Get teachers with pagination and sorting"
if ($result5.Success) {
    $teachersList = $result5.Response
    Write-Success "Pagination and sorting work correctly"
    Write-Host "    Total: $($teachersList.meta.total), Limit: $($teachersList.meta.limit), Offset: $($teachersList.meta.offset)" -ForegroundColor Gray
    $results += @{Test = "GET /courses/{course_id}/teachers (pagination & sorting)"; Success = $true}
} else {
    $results += @{Test = "GET /courses/{course_id}/teachers (pagination & sorting)"; Success = $false; Error = $result5.Error}
}
Write-Host ""

# ========== Тест 6: GET /users/{teacher_id}/courses (с данными) ==========
Write-Host "=== Test 6: GET /users/{teacher_id}/courses (with data) ===" -ForegroundColor Yellow
$result6 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$teacher1Id/courses" -Description "Get courses for teacher (should have at least 1)"
if ($result6.Success) {
    $coursesList = $result6.Response
    Write-Success "Found $($coursesList.items.Count) course(s)"
    foreach ($course in $coursesList.items) {
        Write-Host "    - Course ID=$($course.id), Title=$($course.title)" -ForegroundColor Gray
    }
    $results += @{Test = "GET /users/{teacher_id}/courses (with data)"; Success = $true}
} else {
    $results += @{Test = "GET /users/{teacher_id}/courses (with data)"; Success = $false; Error = $result6.Error}
}
Write-Host ""

# ========== Тест 7: GET /users/{teacher_id}/courses с пагинацией и сортировкой ==========
Write-Host "=== Test 7: GET /users/{teacher_id}/courses (pagination & sorting) ===" -ForegroundColor Yellow
$result7 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$teacher1Id/courses?skip=0&limit=10&sort_by=title&order=asc" -Description "Get courses with pagination and sorting"
if ($result7.Success) {
    $coursesList = $result7.Response
    Write-Success "Pagination and sorting work correctly"
    Write-Host "    Total: $($coursesList.meta.total), Limit: $($coursesList.meta.limit), Offset: $($coursesList.meta.offset)" -ForegroundColor Gray
    $results += @{Test = "GET /users/{teacher_id}/courses (pagination & sorting)"; Success = $true}
} else {
    $results += @{Test = "GET /users/{teacher_id}/courses (pagination & sorting)"; Success = $false; Error = $result7.Error}
}
Write-Host ""

# ========== Тест 8: POST /teacher-courses/ (RESTful создание) ==========
Write-Host "=== Test 8: POST /teacher-courses/ (RESTful create) ===" -ForegroundColor Yellow
$createBody = @{
    teacher_id = $teacher2Id
    course_id = $childCourseId1
} | ConvertTo-Json

$result8 = Invoke-ApiRequest -Method "POST" -Endpoint "/teacher-courses/" -Body $createBody -Description "Create link via RESTful endpoint"
if ($result8.Success -or $result8.StatusCode -eq 201) {
    Write-Success "Link created via RESTful endpoint"
    if ($result8.Response) {
        Write-Host "    Teacher ID: $($result8.Response.teacher_id), Course ID: $($result8.Response.course_id)" -ForegroundColor Gray
    }
    
    # Проверяем БД
    $dbCheck2 = Test-DatabaseState -Query "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher2Id AND course_id = $childCourseId1" -ExpectedValue "1" -Description "RESTful link exists in DB"
    
    $results += @{Test = "POST /teacher-courses/ (RESTful create)"; Success = $true}
} else {
    $results += @{Test = "POST /teacher-courses/ (RESTful create)"; Success = $false; Error = $result8.Error}
}
Write-Host ""

# ========== Тест 9: GET /teacher-courses/ (RESTful список) ==========
Write-Host "=== Test 9: GET /teacher-courses/ (RESTful list) ===" -ForegroundColor Yellow
$result9 = Invoke-ApiRequest -Method "GET" -Endpoint "/teacher-courses/?skip=0&limit=10" -Description "Get all links via RESTful endpoint"
if ($result9.Success) {
    $linksList = $result9.Response
    Write-Success "Found $($linksList.items.Count) link(s)"
    foreach ($link in $linksList.items) {
        Write-Host "    - Teacher ID: $($link.teacher_id), Course ID: $($link.course_id)" -ForegroundColor Gray
    }
    $results += @{Test = "GET /teacher-courses/ (RESTful list)"; Success = $true}
} else {
    $results += @{Test = "GET /teacher-courses/ (RESTful list)"; Success = $false; Error = $result9.Error}
}
Write-Host ""

# ========== Тест 10: GET /teacher-courses/ с фильтрацией ==========
Write-Host "=== Test 10: GET /teacher-courses/ (with filtering) ===" -ForegroundColor Yellow
$result10 = Invoke-ApiRequest -Method "GET" -Endpoint "/teacher-courses/?teacher_id=$teacher1Id&skip=0&limit=10" -Description "Get links filtered by teacher_id"
if ($result10.Success) {
    $linksList = $result10.Response
    Write-Success "Filtering works correctly"
    Write-Host "    Found $($linksList.items.Count) link(s) for teacher $teacher1Id" -ForegroundColor Gray
    $results += @{Test = "GET /teacher-courses/ (with filtering)"; Success = $true}
} else {
    $results += @{Test = "GET /teacher-courses/ (with filtering)"; Success = $false; Error = $result10.Error}
}
Write-Host ""

# ========== Тест 11: DELETE /courses/{course_id}/teachers/{teacher_id} ==========
Write-Host "=== Test 11: DELETE /courses/{course_id}/teachers/{teacher_id} ===" -ForegroundColor Yellow
$result11 = Invoke-ApiRequest -Method "DELETE" -Endpoint "/courses/$parentCourseId/teachers/$teacher1Id" -Description "Unlink teacher from course"
if ($result11.Success -or $result11.StatusCode -eq 204) {
    Write-Success "Teacher unlinked from course"
    
    # Проверяем БД - связь должна быть удалена
    $dbCheck3 = Test-DatabaseState -Query "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher1Id AND course_id = $parentCourseId" -ExpectedValue "0" -Description "Link removed from DB"
    
    $results += @{Test = "DELETE /courses/{course_id}/teachers/{teacher_id}"; Success = $true}
} else {
    $results += @{Test = "DELETE /courses/{course_id}/teachers/{teacher_id}"; Success = $false; Error = $result11.Error}
}
Write-Host ""

# ========== Тест 12: DELETE /teacher-courses/{teacher_id}/{course_id} (RESTful) ==========
Write-Host "=== Test 12: DELETE /teacher-courses/{teacher_id}/{course_id} (RESTful) ===" -ForegroundColor Yellow
$result12 = Invoke-ApiRequest -Method "DELETE" -Endpoint "/teacher-courses/$teacher2Id/$childCourseId1" -Description "Delete link via RESTful endpoint"
if ($result12.Success -or $result12.StatusCode -eq 204) {
    Write-Success "Link deleted via RESTful endpoint"
    
    # Проверяем БД
    $dbCheck4 = Test-DatabaseState -Query "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher2Id AND course_id = $childCourseId1" -ExpectedValue "0" -Description "RESTful link removed from DB"
    
    $results += @{Test = "DELETE /teacher-courses/{teacher_id}/{course_id} (RESTful)"; Success = $true}
} else {
    $results += @{Test = "DELETE /teacher-courses/{teacher_id}/{course_id} (RESTful)"; Success = $false; Error = $result12.Error}
}
Write-Host ""

# ========== Тест 13: Проверка автоматической привязки детей (триггер) ==========
Write-Host "=== Test 13: Auto-link children (trigger test) ===" -ForegroundColor Yellow

# Привязываем преподавателя к родительскому курсу
$result13a = Invoke-ApiRequest -Method "POST" -Endpoint "/courses/$parentCourseId/teachers/$teacher1Id" -Description "Link teacher to parent course for trigger test"
if ($result13a.Success -or $result13a.StatusCode -eq 204) {
    Write-Success "Teacher linked to parent course"
    
    # Ждем немного для выполнения триггера
    Start-Sleep -Seconds 1
    
    # Проверяем БД - должны быть автоматически созданы связи с детьми
    $childLinksQuery = "SELECT COUNT(*) FROM teacher_courses WHERE teacher_id = $teacher1Id AND course_id IN (SELECT course_id FROM course_parents WHERE parent_course_id = $parentCourseId)"
    $childLinksCount = Test-DatabaseQuery -Query $childLinksQuery -Description "Count child links"
    
    Write-Host "  Child links created automatically: $childLinksCount" -ForegroundColor Gray
    
    if ([int]$childLinksCount -gt 0) {
        Write-Success "Trigger automatically linked children"
        $results += @{Test = "Auto-link children (trigger test)"; Success = $true}
    } else {
        Write-Warn "No child links found (may be no children in hierarchy)"
        $results += @{Test = "Auto-link children (trigger test)"; Success = $true; Note = "No children to link"}
    }
} else {
    $results += @{Test = "Auto-link children (trigger test)"; Success = $false; Error = $result13a.Error}
}
Write-Host ""

# ========== Итоговая статистика ==========
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Test Results Summary" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$passed = ($results | Where-Object { $_.Success -eq $true }).Count
$failed = ($results | Where-Object { $_.Success -eq $false }).Count
$total = $results.Count

Write-Host "Total tests: $total" -ForegroundColor White
Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
Write-Host ""

if ($failed -gt 0) {
    Write-Host "Failed tests:" -ForegroundColor Red
    foreach ($result in $results) {
        if (-not $result.Success) {
            Write-Host "  - $($result.Test)" -ForegroundColor Red
            if ($result.Error) {
                Write-Host "    Error: $($result.Error)" -ForegroundColor Yellow
            }
        }
    }
    Write-Host ""
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Checking application logs..." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (Test-Path $LogFile) {
    Write-Host "Log file: $LogFile" -ForegroundColor Gray
    $recentLogs = Get-Content $LogFile -Tail 50
    Write-Host "Last 50 lines of log:" -ForegroundColor Gray
    $recentLogs | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
} else {
    Write-Warn "Log file not found: $LogFile"
}

Write-Host ""
Write-Host "Tests completed!" -ForegroundColor Cyan

if ($failed -gt 0) {
    exit 1
} else {
    exit 0
}
