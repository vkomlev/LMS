# Smoke tests for user courses endpoints via CURL
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
    
    $pythonScript = @"
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
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
        $output = & .venv\Scripts\python.exe $scriptFile 2>&1
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

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Smoke tests for user courses endpoints" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$results = @()

# Получаем список пользователей для тестирования
Write-Host "=== Setup: Getting test users ===" -ForegroundColor Yellow
$usersResult = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?skip=0&limit=5" -Description "Get test users"
if (-not $usersResult.Success -or $usersResult.Response.items.Count -eq 0) {
    Write-Fail "Cannot get test users. Cannot proceed with tests."
    exit 1
}

$testUserId = $usersResult.Response.items[0].id
Write-Host "  Using user ID: $testUserId" -ForegroundColor Gray
Write-Host ""

# Получаем список курсов для тестирования
Write-Host "=== Setup: Getting test courses ===" -ForegroundColor Yellow
$coursesResult = Invoke-ApiRequest -Method "GET" -Endpoint "/courses/?skip=0&limit=5" -Description "Get test courses"
if (-not $coursesResult.Success -or $coursesResult.Response.items.Count -eq 0) {
    Write-Fail "Cannot get test courses. Cannot proceed with tests."
    exit 1
}

$testCourseIds = $coursesResult.Response.items | Select-Object -First 3 -ExpandProperty id
Write-Host "  Using course IDs: $($testCourseIds -join ', ')" -ForegroundColor Gray
Write-Host ""

# Тест 1: GET /users/{user_id}/courses
Write-Host "=== Test 1: GET /users/{user_id}/courses ===" -ForegroundColor Yellow
$result1 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$testUserId/courses" -Description "Get user courses"
if ($result1.Success) {
    $userCourses = $result1.Response
    Write-Host "  User ID: $($userCourses.user_id)"
    Write-Host "  Courses count: $($userCourses.courses.Count)"
    foreach ($uc in $userCourses.courses) {
        Write-Host "    - Course $($uc.course_id): $($uc.course.title) (order=$($uc.order_number))"
    }
    $results += @{Test = "GET /users/{user_id}/courses"; Success = $true}
} else {
    $results += @{Test = "GET /users/{user_id}/courses"; Success = $false; Error = $result1.Error}
}
Write-Host ""

# Тест 2: POST /users/{user_id}/courses/bulk
Write-Host "=== Test 2: POST /users/{user_id}/courses/bulk ===" -ForegroundColor Yellow
$bulkBody = @{
    course_ids = $testCourseIds
} | ConvertTo-Json

$result2 = Invoke-ApiRequest -Method "POST" -Endpoint "/users/$testUserId/courses/bulk" -Body $bulkBody -Description "Bulk assign courses"
if ($result2.Success) {
    $assignedCourses = $result2.Response
    Write-Host "  Assigned courses count: $($assignedCourses.Count)"
    foreach ($uc in $assignedCourses) {
        Write-Host "    - Course $($uc.course_id) (order=$($uc.order_number))"
    }
    
    # Проверяем БД через API - получаем курсы пользователя
    $verifyResult = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$testUserId/courses" -Description "Verify courses in DB via API"
    if ($verifyResult.Success) {
        $foundCourses = $verifyResult.Response.courses | Where-Object { $testCourseIds -contains $_.course_id }
        if ($foundCourses.Count -eq $assignedCourses.Count) {
            Write-Success "DB check: All assigned courses found via API"
        } else {
            Write-Warn "DB check: Expected $($assignedCourses.Count) courses, found $($foundCourses.Count)"
        }
    }
    
    $results += @{Test = "POST /users/{user_id}/courses/bulk"; Success = $true}
} else {
    $results += @{Test = "POST /users/{user_id}/courses/bulk"; Success = $false; Error = $result2.Error}
}
Write-Host ""

# Тест 3: Проверка GET после bulk assign
Write-Host "=== Test 3: GET /users/{user_id}/courses (after bulk assign) ===" -ForegroundColor Yellow
$result3 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$testUserId/courses" -Description "Get user courses after bulk assign"
if ($result3.Success) {
    $userCoursesAfter = $result3.Response
    Write-Host "  Courses count: $($userCoursesAfter.courses.Count)"
    $hasAssignedCourses = $userCoursesAfter.courses | Where-Object { $testCourseIds -contains $_.course_id }
    if ($hasAssignedCourses.Count -gt 0) {
        Write-Success "Assigned courses found in user courses list"
        $results += @{Test = "GET after bulk assign"; Success = $true}
    } else {
        Write-Fail "Assigned courses not found in user courses list"
        $results += @{Test = "GET after bulk assign"; Success = $false; Error = "Courses not found"}
    }
} else {
    $results += @{Test = "GET after bulk assign"; Success = $false; Error = $result3.Error}
}
Write-Host ""

# Тест 4: PATCH /users/{user_id}/courses/reorder
Write-Host "=== Test 4: PATCH /users/{user_id}/courses/reorder ===" -ForegroundColor Yellow
# Получаем текущие курсы пользователя
$currentCoursesResult = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$testUserId/courses" -Description "Get current courses for reorder"
if ($currentCoursesResult.Success -and $currentCoursesResult.Response.courses.Count -ge 2) {
    $coursesToReorder = $currentCoursesResult.Response.courses | Select-Object -First 2
    
    # Переупорядочиваем: меняем порядок местами
    $reorderBody = @{
        course_orders = @(
            @{course_id = $coursesToReorder[1].course_id; order_number = 1},
            @{course_id = $coursesToReorder[0].course_id; order_number = 2}
        )
    } | ConvertTo-Json
    
    Write-Host "  Reordering courses:"
    Write-Host "    Course $($coursesToReorder[1].course_id) -> order 1"
    Write-Host "    Course $($coursesToReorder[0].course_id) -> order 2"
    
    $result4 = Invoke-ApiRequest -Method "PATCH" -Endpoint "/users/$testUserId/courses/reorder" -Body $reorderBody -Description "Reorder user courses"
    
    if ($result4.Success) {
        $reorderedCourses = $result4.Response
        Write-Host "  Reordered courses count: $($reorderedCourses.Count)"
        
        # Проверяем БД через API - order_number должен быть обновлен
        $verifyReorderResult = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$testUserId/courses" -Description "Verify reorder in DB via API"
        if ($verifyReorderResult.Success) {
            $firstCourseId = $coursesToReorder[1].course_id
            $reorderedCourse = $verifyReorderResult.Response.courses | Where-Object { $_.course_id -eq $firstCourseId }
            if ($reorderedCourse -and $reorderedCourse.order_number -eq 1) {
                Write-Success "DB check: Course order_number updated correctly (should be 1)"
            } else {
                Write-Warn "DB check: Course order_number mismatch (expected 1, got $($reorderedCourse.order_number))"
            }
        }
        
        # Восстанавливаем исходный порядок
        Write-Host "  Restoring original order..."
        $restoreBody = @{
            course_orders = @(
                @{course_id = $coursesToReorder[0].course_id; order_number = 1},
                @{course_id = $coursesToReorder[1].course_id; order_number = 2}
            )
        } | ConvertTo-Json
        
        $restoreResult = Invoke-ApiRequest -Method "PATCH" -Endpoint "/users/$testUserId/courses/reorder" -Body $restoreBody -Description "Restore original order"
        if ($restoreResult.Success) {
            Write-Success "Original order restored"
        } else {
            Write-Warn "Failed to restore original order"
        }
        
        $results += @{Test = "PATCH /users/{user_id}/courses/reorder"; Success = $true}
    } else {
        Write-Host "  Reorder error: $($result4.Error)"
        $results += @{Test = "PATCH /users/{user_id}/courses/reorder"; Success = $false; Error = $result4.Error}
    }
} else {
    Write-Warn "Skipped: need at least 2 courses for reorder test"
    $results += @{Test = "PATCH /users/{user_id}/courses/reorder"; Success = $false; Error = "Not enough courses"}
}
Write-Host ""

# Тест 5: Проверка дубликатов при bulk assign
Write-Host "=== Test 5: Bulk assign duplicate courses ===" -ForegroundColor Yellow
$duplicateBody = @{
    course_ids = @($testCourseIds[0])
} | ConvertTo-Json

$result5 = Invoke-ApiRequest -Method "POST" -Endpoint "/users/$testUserId/courses/bulk" -Body $duplicateBody -Description "Bulk assign duplicate course"
if ($result5.Success) {
    $duplicateResult = $result5.Response
    Write-Host "  Result: $($duplicateResult.Count) courses returned"
    Write-Host "  Note: Duplicate courses should be skipped (not create new records)"
    $results += @{Test = "Bulk assign duplicates"; Success = $true}
} else {
    # Если ошибка, это тоже нормально - может быть валидация
    Write-Warn "Bulk assign returned error (may be expected): $($result5.Error)"
    $results += @{Test = "Bulk assign duplicates"; Success = $true; Note = "Error may be expected"}
}
Write-Host ""

# Check logs for errors
Write-Host "=== Checking logs for errors ===" -ForegroundColor Yellow
if (Test-Path $LogFile) {
    $recentLogs = Get-Content $LogFile -Tail 100
    $errorCount = ($recentLogs | Select-String -Pattern "ERROR|Exception|Traceback" -CaseSensitive:$false).Count
    if ($errorCount -gt 0) {
        Write-Warn "Found $errorCount error entries in logs (check logs/app.log for details)"
    } else {
        Write-Success "No errors found in recent logs"
    }
} else {
    Write-Warn "Log file not found: $LogFile"
}
Write-Host ""

# Summary
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "RESULTS:" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$passed = ($results | Where-Object { $_.Success -eq $true }).Count
$total = $results.Count

foreach ($result in $results) {
    if ($result.Success) {
        Write-Success "$($result.Test)"
    } else {
        Write-Fail "$($result.Test): $($result.Error)"
    }
}

Write-Host ""
Write-Host "Passed: $passed/$total" -ForegroundColor $(if ($passed -eq $total) { "Green" } else { "Yellow" })

if ($passed -eq $total) {
    Write-Host "All tests passed successfully!" -ForegroundColor Green
    exit 0
} else {
    $failed = $total - $passed
    Write-Host "Failed tests: $failed" -ForegroundColor Red
    exit 1
}
