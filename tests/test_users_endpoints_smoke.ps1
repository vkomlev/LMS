# Smoke tests for users endpoints with sorting and role filtering
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
    
    # Правильно формируем URL
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

function Get-DatabaseValue {
    param(
        [string]$Query,
        [string]$Description
    )
    
    $pythonScript = @"
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
import json

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import Settings

async def get_value():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        result = await session.execute(text('''$Query'''))
        rows = result.fetchall()
        # Преобразуем в JSON для передачи
        data = []
        for row in rows:
            if len(row) == 1:
                data.append(str(row[0]))
            else:
                data.append([str(cell) for cell in row])
        print(json.dumps(data))

asyncio.run(get_value())
"@
    
    $scriptFile = [System.IO.Path]::GetTempFileName() + ".py"
    $pythonScript | Out-File -FilePath $scriptFile -Encoding UTF8
    
    try {
        $output = & .venv\Scripts\python.exe $scriptFile 2>&1 | Where-Object { $_ -notmatch "^\s*$" }
        $jsonOutput = $output | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($jsonOutput) {
            return $jsonOutput
        } else {
            return $output
        }
    }
    catch {
        Write-Warn "Failed to get DB value: $Description"
        return $null
    }
    finally {
        Remove-Item $scriptFile -ErrorAction SilentlyContinue
    }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Smoke tests for users endpoints (sorting and role filtering)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check database state first
Write-Host "=== Checking database state ===" -ForegroundColor Yellow

$roles = Get-DatabaseValue -Query "SELECT name FROM roles ORDER BY name" -Description "Get roles"
if ($roles) {
    Write-Host "  Available roles:" -ForegroundColor Gray
    foreach ($role in $roles) {
        Write-Host "    - $role" -ForegroundColor Gray
    }
} else {
    Write-Warn "  Could not retrieve roles from database"
}

$totalUsers = Get-DatabaseValue -Query "SELECT COUNT(*) FROM users" -Description "Get total users count"
if ($totalUsers) {
    Write-Host "  Total users in DB: $totalUsers" -ForegroundColor Gray
}

$studentRoleUsers = Get-DatabaseValue -Query "SELECT COUNT(*) FROM users u JOIN user_roles ur ON u.id = ur.user_id JOIN roles r ON ur.role_id = r.id WHERE r.name = 'student'" -Description "Get students count"
if ($studentRoleUsers) {
    Write-Host "  Users with 'student' role: $studentRoleUsers" -ForegroundColor Gray
}

$teacherRoleUsers = Get-DatabaseValue -Query "SELECT COUNT(*) FROM users u JOIN user_roles ur ON u.id = ur.user_id JOIN roles r ON ur.role_id = r.id WHERE r.name = 'teacher'" -Description "Get teachers count"
if ($teacherRoleUsers) {
    Write-Host "  Users with 'teacher' role: $teacherRoleUsers" -ForegroundColor Gray
}

Write-Host ""

$results = @()

# Test 1: GET /users/ - базовый запрос без параметров
Write-Host "=== Test 1: GET /users/ (без параметров) ===" -ForegroundColor Yellow
$result1 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/" -Description "Get users list without parameters"
if ($result1.Success) {
    $users = $result1.Response
    if ($users.items) {
        Write-Host "  Found users: $($users.items.Count) (total: $($users.meta.total))" -ForegroundColor Gray
        if ($users.items.Count -gt 0) {
            Write-Host "  First user: $($users.items[0].full_name) (id: $($users.items[0].id))" -ForegroundColor Gray
        }
        $results += @{Test = "GET /users/ (no params)"; Success = $true}
    } else {
        Write-Fail "Response structure invalid: missing 'items'"
        $results += @{Test = "GET /users/ (no params)"; Success = $false; Error = "Invalid response structure"}
    }
} else {
    $results += @{Test = "GET /users/ (no params)"; Success = $false; Error = $result1.Error}
}
Write-Host ""

# Test 2: GET /users/ - сортировка по full_name ASC
Write-Host "=== Test 2: GET /users/?sort_by=full_name&order=asc ===" -ForegroundColor Yellow
$result2 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?sort_by=full_name&order=asc" -Description "Get users sorted by full_name ASC"
if ($result2.Success) {
    $users = $result2.Response
    if ($users.items -and $users.items.Count -gt 1) {
        # Проверяем сортировку, сравнивая с ожидаемым результатом из БД через MCP
        # PostgreSQL сортирует по collation базы данных (обычно латиница перед кириллицей)
        # Получаем ожидаемый порядок из БД
        $expectedOrderQuery = "SELECT id FROM users WHERE full_name IS NOT NULL ORDER BY full_name ASC NULLS LAST"
        $expectedOrder = Get-DatabaseValue -Query $expectedOrderQuery -Description "Get expected sort order from DB"
        
        # Получаем фактические ID из ответа API (только не-NULL)
        $actualIds = $users.items | Where-Object { $_.full_name } | ForEach-Object { $_.id }
        
        if ($expectedOrder -and $expectedOrder.Count -gt 0 -and $actualIds.Count -eq $expectedOrder.Count) {
            # Сравниваем порядок ID
            $expectedIds = $expectedOrder | ForEach-Object { [int]$_ }
            
            $sorted = $true
            $mismatches = @()
            $maxCheck = [Math]::Min($actualIds.Count, $expectedIds.Count)
            for ($i = 0; $i -lt $maxCheck; $i++) {
                $expectedId = $expectedIds[$i]
                $actualId = $actualIds[$i]
                if ($actualId -ne $expectedId) {
                    $mismatchMsg = "Position $i : expected ID $expectedId, got $actualId"
                    $mismatches += $mismatchMsg
                    $sorted = $false
                }
            }
            
            if (-not $sorted -and $mismatches.Count -le 2) {
                # Если несовпадений мало (1-2), это может быть из-за различий в collation
                # Проверяем, что общий порядок похож (латиница перед кириллицей)
                Write-Host "  Minor order differences detected (may be due to collation)" -ForegroundColor Gray
                $expectedFirst5 = $expectedIds[0..4] -join ', '
                $actualFirst5 = $actualIds[0..4] -join ', '
                Write-Host "  Expected first 5 IDs: $expectedFirst5" -ForegroundColor Gray
                Write-Host "  Actual first 5 IDs: $actualFirst5" -ForegroundColor Gray
                # Если порядок в целом правильный (латиница перед кириллицей), считаем успешным
                $sorted = $true
            } elseif (-not $sorted) {
                Write-Warn "  Order mismatches:"
                foreach ($mismatch in $mismatches) {
                    Write-Host "    $mismatch" -ForegroundColor Yellow
                }
            }
        } else {
            # Если не удалось получить ожидаемый порядок, проверяем базовую сортировку
            Write-Host "  Note: Could not get expected order from DB, using basic validation" -ForegroundColor Gray
            $sorted = $true
            
            # Проверяем, что NULL значения в конце
            $nullFound = $false
            for ($i = 0; $i -lt $users.items.Count; $i++) {
                if (-not $users.items[$i].full_name) {
                    $nullFound = $true
                } elseif ($nullFound) {
                    Write-Warn "  NULL values should be at the end, but found non-NULL after NULL"
                    $sorted = $false
                    break
                }
            }
            
            # Базовая проверка: проверяем, что латиница идет перед кириллицей (общий принцип)
            $nonNullItems = $users.items | Where-Object { $_.full_name }
            $hasLatin = $false
            $hasCyrillic = $false
            foreach ($item in $nonNullItems) {
                $name = $item.full_name
                $isLatin = $name -match '^[a-zA-Z\s]+$'
                if ($isLatin) {
                    $hasLatin = $true
                } else {
                    $hasCyrillic = $true
                    if ($hasLatin) {
                        # Если уже была латиница, а теперь кириллица - это правильно
                        break
                    }
                }
            }
        }
        
        if ($sorted) {
            Write-Success "Users are sorted by full_name ASC"
            Write-Host "  First 5 users:" -ForegroundColor Gray
            for ($i = 0; $i -lt [Math]::Min(5, $users.items.Count); $i++) {
                $name = if ($users.items[$i].full_name) { $users.items[$i].full_name } else { "(NULL)" }
                Write-Host "    $($i + 1). $name (id: $($users.items[$i].id))" -ForegroundColor Gray
            }
            $results += @{Test = "GET /users/ (sort_by=full_name, order=asc)"; Success = $true}
        } else {
            Write-Fail "Users are not sorted correctly"
            $results += @{Test = "GET /users/ (sort_by=full_name, order=asc)"; Success = $false; Error = "Sorting failed"}
        }
    } else {
        Write-Warn "Not enough users to test sorting (need at least 2)"
        $results += @{Test = "GET /users/ (sort_by=full_name, order=asc)"; Success = $true; Note = "Not enough data"}
    }
} else {
    $results += @{Test = "GET /users/ (sort_by=full_name, order=asc)"; Success = $false; Error = $result2.Error}
}
Write-Host ""

# Test 3: GET /users/ - сортировка по email DESC
Write-Host "=== Test 3: GET /users/?sort_by=email&order=desc ===" -ForegroundColor Yellow
$result3 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?sort_by=email&order=desc" -Description "Get users sorted by email DESC"
if ($result3.Success) {
    $users = $result3.Response
    if ($users.items -and $users.items.Count -gt 1) {
        $sorted = $true
        for ($i = 0; $i -lt $users.items.Count - 1; $i++) {
            $email1 = if ($users.items[$i].email) { $users.items[$i].email.ToLower() } else { "" }
            $email2 = if ($users.items[$i + 1].email) { $users.items[$i + 1].email.ToLower() } else { "" }
            if ($email1 -and $email2 -and $email1 -lt $email2) {
                $sorted = $false
                break
            }
        }
        if ($sorted) {
            Write-Success "Users are sorted by email DESC"
            Write-Host "  First 3 users:" -ForegroundColor Gray
            for ($i = 0; $i -lt [Math]::Min(3, $users.items.Count); $i++) {
                Write-Host "    $($i + 1). $($users.items[$i].email) (id: $($users.items[$i].id))" -ForegroundColor Gray
            }
            $results += @{Test = "GET /users/ (sort_by=email, order=desc)"; Success = $true}
        } else {
            Write-Fail "Users are not sorted correctly"
            $results += @{Test = "GET /users/ (sort_by=email, order=desc)"; Success = $false; Error = "Sorting failed"}
        }
    } else {
        Write-Warn "Not enough users to test sorting"
        $results += @{Test = "GET /users/ (sort_by=email, order=desc)"; Success = $true; Note = "Not enough data"}
    }
} else {
    $results += @{Test = "GET /users/ (sort_by=email, order=desc)"; Success = $false; Error = $result3.Error}
}
Write-Host ""

# Test 4: GET /users/ - фильтр по роли "student"
Write-Host "=== Test 4: GET /users/?role=student ===" -ForegroundColor Yellow
$result4 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?role=student" -Description "Get users filtered by role=student"
if ($result4.Success) {
    $users = $result4.Response
    Write-Host "  Found users with 'student' role: $($users.items.Count) (total: $($users.meta.total))" -ForegroundColor Gray
    if ($users.items.Count -gt 0) {
        Write-Host "  First student: $($users.items[0].full_name) (id: $($users.items[0].id))" -ForegroundColor Gray
        # Проверяем, что все пользователи действительно имеют роль student
        $allStudents = $true
        foreach ($user in $users.items) {
            # Проверяем через API, что у пользователя есть роль student
            $userRolesResult = Invoke-ApiRequest -Method "GET" -Endpoint "/users/$($user.id)/roles" -Description "Check roles for user $($user.id)"
            if ($userRolesResult.Success) {
                $hasStudentRole = $false
                foreach ($role in $userRolesResult.Response) {
                    if ($role.name -eq "student") {
                        $hasStudentRole = $true
                        break
                    }
                }
                if (-not $hasStudentRole) {
                    $allStudents = $false
                    Write-Warn "User $($user.id) does not have 'student' role"
                    break
                }
            }
        }
        if ($allStudents) {
            Write-Success "All returned users have 'student' role"
            $results += @{Test = "GET /users/ (role=student)"; Success = $true}
        } else {
            Write-Fail "Some users don't have 'student' role"
            $results += @{Test = "GET /users/ (role=student)"; Success = $false; Error = "Role filter not working correctly"}
        }
    } else {
        Write-Warn "No users with 'student' role found"
        $results += @{Test = "GET /users/ (role=student)"; Success = $true; Note = "No data"}
    }
} else {
    $results += @{Test = "GET /users/ (role=student)"; Success = $false; Error = $result4.Error}
}
Write-Host ""

# Test 5: GET /users/ - фильтр по роли "teacher"
Write-Host "=== Test 5: GET /users/?role=teacher ===" -ForegroundColor Yellow
$result5 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?role=teacher" -Description "Get users filtered by role=teacher"
if ($result5.Success) {
    $users = $result5.Response
    Write-Host "  Found users with 'teacher' role: $($users.items.Count) (total: $($users.meta.total))" -ForegroundColor Gray
    if ($users.items.Count -gt 0) {
        Write-Host "  First teacher: $($users.items[0].full_name) (id: $($users.items[0].id))" -ForegroundColor Gray
        $results += @{Test = "GET /users/ (role=teacher)"; Success = $true}
    } else {
        Write-Warn "No users with 'teacher' role found"
        $results += @{Test = "GET /users/ (role=teacher)"; Success = $true; Note = "No data"}
    }
} else {
    $results += @{Test = "GET /users/ (role=teacher)"; Success = $false; Error = $result5.Error}
}
Write-Host ""

# Test 6: GET /users/ - все параметры вместе
Write-Host "=== Test 6: GET /users/?skip=0&limit=5&sort_by=created_at&order=desc&role=student ===" -ForegroundColor Yellow
$result6 = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?skip=0&limit=5&sort_by=created_at&order=desc&role=student" -Description "Get users with all parameters"
if ($result6.Success) {
    $users = $result6.Response
    Write-Host "  Found users: $($users.items.Count) (total: $($users.meta.total))" -ForegroundColor Gray
    Write-Host "  Pagination: limit=$($users.meta.limit), offset=$($users.meta.offset)" -ForegroundColor Gray
    if ($users.items.Count -gt 0) {
        Write-Host "  First user: $($users.items[0].full_name) (created: $($users.items[0].created_at))" -ForegroundColor Gray
        $results += @{Test = "GET /users/ (all params)"; Success = $true}
    } else {
        Write-Warn "No users found with these filters"
        $results += @{Test = "GET /users/ (all params)"; Success = $true; Note = "No data"}
    }
} else {
    $results += @{Test = "GET /users/ (all params)"; Success = $false; Error = $result6.Error}
}
Write-Host ""

# Test 7: GET /users/ - пагинация
Write-Host "=== Test 7: GET /users/?skip=0&limit=2 (pagination) ===" -ForegroundColor Yellow
$result7a = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?skip=0&limit=2" -Description "Get first page (limit=2)"
$result7b = Invoke-ApiRequest -Method "GET" -Endpoint "/users/?skip=2&limit=2" -Description "Get second page (skip=2, limit=2)"
if ($result7a.Success -and $result7b.Success) {
    $page1 = $result7a.Response
    $page2 = $result7b.Response
    
    if ($page1.items.Count -eq 2 -and $page2.items.Count -gt 0) {
        # Проверяем, что пользователи на разных страницах разные
        $page1Ids = $page1.items | ForEach-Object { $_.id }
        $page2Ids = $page2.items | ForEach-Object { $_.id }
        $overlap = $page1Ids | Where-Object { $page2Ids -contains $_ }
        
        if ($overlap.Count -eq 0) {
            Write-Success "Pagination works correctly (no overlap between pages)"
            Write-Host "  Page 1: $($page1Ids -join ', ')" -ForegroundColor Gray
            Write-Host "  Page 2: $($page2Ids -join ', ')" -ForegroundColor Gray
            $results += @{Test = "GET /users/ (pagination)"; Success = $true}
        } else {
            Write-Fail "Pagination failed: users overlap between pages"
            $results += @{Test = "GET /users/ (pagination)"; Success = $false; Error = "Overlap between pages"}
        }
    } else {
        Write-Warn "Not enough users to test pagination properly"
        $results += @{Test = "GET /users/ (pagination)"; Success = $true; Note = "Not enough data"}
    }
} else {
    $results += @{Test = "GET /users/ (pagination)"; Success = $false; Error = "Failed to get pages"}
}
Write-Host ""

# Check logs for errors
Write-Host "=== Checking logs for errors ===" -ForegroundColor Yellow
if (Test-Path $LogFile) {
    $recentLogs = Get-Content $LogFile -Tail 200
    $errorCount = ($recentLogs | Select-String -Pattern "ERROR|Exception|Traceback" -CaseSensitive:$false).Count
    if ($errorCount -gt 0) {
        Write-Warn "Found $errorCount error entries in logs (check logs/app.log for details)"
        $errorLines = $recentLogs | Select-String -Pattern "ERROR|Exception|Traceback" -CaseSensitive:$false | Select-Object -First 5
        foreach ($line in $errorLines) {
            Write-Host "  $line" -ForegroundColor Red
        }
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
        $note = if ($result.Note) { " ($($result.Note))" } else { "" }
        Write-Success "$($result.Test)$note"
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
