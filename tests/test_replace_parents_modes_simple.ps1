# Tests for replace_parents modes
# Mode 1: replace_parents=false (default) - add new links to existing
# Mode 2: replace_parents=true - replace all existing links with new

$apiKey = "bot-key-1"
$baseUrl = "http://localhost:8000/api/v1"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Tests for replace_parents modes" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Get test courses from DB
Write-Host "[INFO] Preparing test data..." -ForegroundColor Yellow

$testDataOutput = & .venv\Scripts\python.exe tests\get_test_courses_for_replace_test.py 2>&1 | Out-String

$courseWithoutParent1 = if ($testDataOutput -match "COURSE_WITHOUT_PARENT_1=(\d+)") { $matches[1] } else { $null }
$courseWithoutParent2 = if ($testDataOutput -match "COURSE_WITHOUT_PARENT_2=(\d+)") { $matches[1] } else { $null }
$courseWithoutParent3 = if ($testDataOutput -match "COURSE_WITHOUT_PARENT_3=(\d+)") { $matches[1] } else { $null }
$courseWithParent = if ($testDataOutput -match "COURSE_WITH_PARENT=(\d+)") { $matches[1] } else { $null }
$parentOfCourseWithParent = if ($testDataOutput -match "PARENT_OF_COURSE_WITH_PARENT=(\d+)") { $matches[1] } else { $null }

if (-not $courseWithoutParent1 -or -not $courseWithoutParent2 -or -not $courseWithParent) {
    Write-Host "[ERROR] Not enough test courses found!" -ForegroundColor Red
    Write-Host "Found:" -ForegroundColor Yellow
    Write-Host "  Course without parent 1: $courseWithoutParent1" -ForegroundColor Yellow
    Write-Host "  Course without parent 2: $courseWithoutParent2" -ForegroundColor Yellow
    Write-Host "  Course with parent: $courseWithParent" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Test data:" -ForegroundColor Green
Write-Host "  Course without parent 1: $courseWithoutParent1" -ForegroundColor Green
Write-Host "  Course without parent 2: $courseWithoutParent2" -ForegroundColor Green
Write-Host "  Course without parent 3: $courseWithoutParent3" -ForegroundColor Green
Write-Host "  Course with parent: $courseWithParent (parent: $parentOfCourseWithParent)" -ForegroundColor Green
Write-Host ""

# Test 1: Add mode (replace_parents=false, default)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Test 1: Add mode (replace_parents=false)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check current state
Write-Host "[STEP 1.1] Checking current state of course $courseWithParent..." -ForegroundColor Yellow
$checkBefore = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent" -Method GET -Headers @{"api_key"=$apiKey} -ErrorAction SilentlyContinue
if (-not $checkBefore) {
    $checkBefore = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET
}
Write-Host "  Current parents: $($checkBefore.parent_courses.Count)" -ForegroundColor Gray
foreach ($parent in $checkBefore.parent_courses) {
    Write-Host "    - Parent ID: $($parent.id)" -ForegroundColor Gray
}

# Add new parent without replace_parents (default false)
Write-Host ""
Write-Host "[STEP 1.2] Adding new parent $courseWithoutParent1 to course $courseWithParent (replace_parents not specified)..." -ForegroundColor Yellow
$body1 = @{
    parent_course_ids = @($courseWithoutParent1)
} | ConvertTo-Json

try {
    $response1 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method PATCH -Body $body1 -ContentType "application/json" -ErrorAction Stop
    Write-Host "[OK] Request completed successfully" -ForegroundColor Green
    
    # Check result
    Write-Host "[STEP 1.3] Checking result..." -ForegroundColor Yellow
    $checkAfter1 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET -ErrorAction Stop
    $parentIds = $checkAfter1.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Parents after add: $($parentIds -join ', ')" -ForegroundColor Gray
    
    if ($parentIds -contains $courseWithoutParent1 -and $parentIds -contains $parentOfCourseWithParent) {
        Write-Host "[OK] Add mode works correctly: new parent added, old ones preserved" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Expected parents: $parentOfCourseWithParent and $courseWithoutParent1" -ForegroundColor Red
        Write-Host "  Got: $($parentIds -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Request failed: $_" -ForegroundColor Red
    $_.Exception.Response | Format-List -Force
}

Write-Host ""

# Test 2: Replace mode (replace_parents=true)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Test 2: Replace mode (replace_parents=true)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check current state before replace
Write-Host "[STEP 2.1] Checking current state of course $courseWithParent..." -ForegroundColor Yellow
$checkBefore2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET -ErrorAction Stop
$parentIdsBefore = $checkBefore2.parent_courses | ForEach-Object { $_.id }
Write-Host "  Current parents: $($parentIdsBefore -join ', ')" -ForegroundColor Gray

# Replace all parents with new ones
Write-Host ""
Write-Host "[STEP 2.2] Replacing all parents of course $courseWithParent with $courseWithoutParent2 and $courseWithoutParent3 (replace_parents=true)..." -ForegroundColor Yellow
$body2 = @{
    parent_course_ids = @($courseWithoutParent2, $courseWithoutParent3)
    replace_parents = $true
} | ConvertTo-Json

try {
    $response2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method PATCH -Body $body2 -ContentType "application/json" -ErrorAction Stop
    Write-Host "[OK] Request completed successfully" -ForegroundColor Green
    
    # Check result
    Write-Host "[STEP 2.3] Checking result..." -ForegroundColor Yellow
    $checkAfter2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET -ErrorAction Stop
    $parentIdsAfter = $checkAfter2.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Parents after replace: $($parentIdsAfter -join ', ')" -ForegroundColor Gray
    
    $expectedParents = @($courseWithoutParent2, $courseWithoutParent3) | Sort-Object
    $actualParents = $parentIdsAfter | Sort-Object
    
    if ((Compare-Object $expectedParents $actualParents -PassThru | Measure-Object).Count -eq 0) {
        Write-Host "[OK] Replace mode works correctly: all old parents replaced with new ones" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Expected: $($expectedParents -join ', ')" -ForegroundColor Red
        Write-Host "  Got: $($actualParents -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Request failed: $_" -ForegroundColor Red
    $_.Exception.Response | Format-List -Force
}

Write-Host ""

# Test 3: Test via /move endpoint
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Test 3: Modes via /move endpoint" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Use different course for /move test
$testCourseId = $courseWithoutParent1

# Test 3.1: Add via /move
Write-Host "[STEP 3.1] Adding parent via /move (replace_parents=false)..." -ForegroundColor Yellow
$body3_1 = @{
    new_parent_ids = @($courseWithoutParent2)
    replace_parents = $false
} | ConvertTo-Json

try {
    $response3_1 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId/move?api_key=$apiKey" -Method PATCH -Body $body3_1 -ContentType "application/json" -ErrorAction Stop
    Write-Host "[OK] Request completed successfully" -ForegroundColor Green
    
    $check3_1 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId?api_key=$apiKey" -Method GET -ErrorAction Stop
    $parentIds3_1 = $check3_1.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Parents after add: $($parentIds3_1 -join ', ')" -ForegroundColor Gray
    
    if ($parentIds3_1 -contains $courseWithoutParent2) {
        Write-Host "[OK] Add via /move works correctly" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Parent not added" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Request failed: $_" -ForegroundColor Red
}

Write-Host ""

# Test 3.2: Replace via /move
Write-Host "[STEP 3.2] Replacing parents via /move (replace_parents=true)..." -ForegroundColor Yellow
$body3_2 = @{
    new_parent_ids = @($courseWithoutParent3)
    replace_parents = $true
} | ConvertTo-Json

try {
    $response3_2 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId/move?api_key=$apiKey" -Method PATCH -Body $body3_2 -ContentType "application/json"
    Write-Host "[OK] Request completed successfully" -ForegroundColor Green
    
    $check3_2 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId?api_key=$apiKey" -Method GET
    $parentIds3_2 = $check3_2.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Parents after replace: $($parentIds3_2 -join ', ')" -ForegroundColor Gray
    
    if ($parentIds3_2.Count -eq 1 -and $parentIds3_2[0] -eq $courseWithoutParent3) {
        Write-Host "[OK] Replace via /move works correctly" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Expected only parent $courseWithoutParent3, got: $($parentIds3_2 -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Request failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Checking logs and DB" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check logs
Write-Host "[INFO] Checking logs..." -ForegroundColor Yellow
if (Test-Path "logs/app.log") {
    $logContent = Get-Content "logs/app.log" -Tail 50 -Encoding UTF8
    $errorLines = $logContent | Select-String -Pattern "ERROR|Exception|Traceback" -Context 2
    if ($errorLines) {
        Write-Host "[WARN] Found errors in logs:" -ForegroundColor Yellow
        $errorLines | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    } else {
        Write-Host "[OK] No critical errors found in logs" -ForegroundColor Green
    }
} else {
    Write-Host "[WARN] Log file not found" -ForegroundColor Yellow
}

# Check DB via Python
Write-Host ""
Write-Host "[INFO] Checking DB state..." -ForegroundColor Yellow
$dbCheckResult = & .venv\Scripts\python.exe tests\check_db_state_for_replace_test.py $courseWithParent $testCourseId 2>&1 | Out-String
Write-Host $dbCheckResult

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Tests completed" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
