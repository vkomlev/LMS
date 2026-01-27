# Тесты для проверки режимов работы replace_parents
# Режим 1: replace_parents=false (по умолчанию) - добавление новых связей к существующим
# Режим 2: replace_parents=true - замена всех существующих связей новыми

$apiKey = "bot-key-1"
$baseUrl = "http://localhost:8000/api/v1"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Тесты режимов работы replace_parents" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Шаг 1: Найти тестовые курсы через прямой запрос к БД
Write-Host "[INFO] Подготовка тестовых данных..." -ForegroundColor Yellow

# Используем Python для получения данных из БД
$testData = python tests\get_test_courses_for_replace_test.py 2>&1

$courseWithoutParent1 = ($testData | Select-String "COURSE_WITHOUT_PARENT_1=(\d+)").Matches.Groups[1].Value
$courseWithoutParent2 = ($testData | Select-String "COURSE_WITHOUT_PARENT_2=(\d+)").Matches.Groups[1].Value
$courseWithoutParent3 = ($testData | Select-String "COURSE_WITHOUT_PARENT_3=(\d+)").Matches.Groups[1].Value
$courseWithParent = ($testData | Select-String "COURSE_WITH_PARENT=(\d+)").Matches.Groups[1].Value
$parentOfCourseWithParent = ($testData | Select-String "PARENT_OF_COURSE_WITH_PARENT=(\d+)").Matches.Groups[1].Value

if (-not $courseWithoutParent1 -or -not $courseWithoutParent2 -or -not $courseWithParent) {
    Write-Host "[ERROR] Не найдено достаточно тестовых курсов!" -ForegroundColor Red
    Write-Host "Найдено:" -ForegroundColor Yellow
    Write-Host "  Курс без родителей 1: $courseWithoutParent1" -ForegroundColor Yellow
    Write-Host "  Курс без родителей 2: $courseWithoutParent2" -ForegroundColor Yellow
    Write-Host "  Курс с родителями: $courseWithParent" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Тестовые данные:" -ForegroundColor Green
Write-Host "  Курс без родителей 1: $courseWithoutParent1" -ForegroundColor Green
Write-Host "  Курс без родителей 2: $courseWithoutParent2" -ForegroundColor Green
Write-Host "  Курс без родителей 3: $courseWithoutParent3" -ForegroundColor Green
Write-Host "  Курс с родителями: $courseWithParent (родитель: $parentOfCourseWithParent)" -ForegroundColor Green
Write-Host ""

# Тест 1: Режим добавления (replace_parents=false, по умолчанию)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Тест 1: Режим добавления (replace_parents=false)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Сначала проверяем текущее состояние курса
Write-Host "[STEP 1.1] Проверка текущего состояния курса $courseWithParent..." -ForegroundColor Yellow
$checkBefore = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET
Write-Host "  Текущие родители: $($checkBefore.parent_courses.Count)" -ForegroundColor Gray
foreach ($parent in $checkBefore.parent_courses) {
    Write-Host "    - Родитель ID: $($parent.id)" -ForegroundColor Gray
}

# Добавляем нового родителя без replace_parents (по умолчанию false)
Write-Host ""
Write-Host "[STEP 1.2] Добавление нового родителя $courseWithoutParent1 к курсу $courseWithParent (replace_parents не указан)..." -ForegroundColor Yellow
$body1 = @{
    parent_course_ids = @($courseWithoutParent1)
} | ConvertTo-Json

try {
    $response1 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method PATCH -Body $body1 -ContentType "application/json"
    Write-Host "[OK] Запрос выполнен успешно" -ForegroundColor Green
    
    # Проверяем результат
    Write-Host "[STEP 1.3] Проверка результата..." -ForegroundColor Yellow
    $checkAfter1 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET
    $parentIds = $checkAfter1.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Родители после добавления: $($parentIds -join ', ')" -ForegroundColor Gray
    
    if ($parentIds -contains $courseWithoutParent1 -and $parentIds -contains $parentOfCourseWithParent) {
        Write-Host "[OK] Режим добавления работает корректно: новый родитель добавлен, старые сохранены" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Ожидалось, что будут родители: $parentOfCourseWithParent и $courseWithoutParent1" -ForegroundColor Red
        Write-Host "  Получено: $($parentIds -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Ошибка при выполнении запроса: $_" -ForegroundColor Red
    $_.Exception.Response | Format-List -Force
}

Write-Host ""

# Тест 2: Режим замены (replace_parents=true)
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Тест 2: Режим замены (replace_parents=true)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Проверяем текущее состояние перед заменой
Write-Host "[STEP 2.1] Проверка текущего состояния курса $courseWithParent..." -ForegroundColor Yellow
$checkBefore2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET
$parentIdsBefore = $checkBefore2.parent_courses | ForEach-Object { $_.id }
Write-Host "  Текущие родители: $($parentIdsBefore -join ', ')" -ForegroundColor Gray

# Заменяем всех родителей новыми
Write-Host ""
Write-Host "[STEP 2.2] Замена всех родителей курса $courseWithParent на $courseWithoutParent2 и $courseWithoutParent3 (replace_parents=true)..." -ForegroundColor Yellow
$body2 = @{
    parent_course_ids = @($courseWithoutParent2, $courseWithoutParent3)
    replace_parents = $true
} | ConvertTo-Json

try {
    $response2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method PATCH -Body $body2 -ContentType "application/json"
    Write-Host "[OK] Запрос выполнен успешно" -ForegroundColor Green
    
    # Проверяем результат
    Write-Host "[STEP 2.3] Проверка результата..." -ForegroundColor Yellow
    $checkAfter2 = Invoke-RestMethod -Uri "$baseUrl/courses/$courseWithParent?api_key=$apiKey" -Method GET
    $parentIdsAfter = $checkAfter2.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Родители после замены: $($parentIdsAfter -join ', ')" -ForegroundColor Gray
    
    $expectedParents = @($courseWithoutParent2, $courseWithoutParent3) | Sort-Object
    $actualParents = $parentIdsAfter | Sort-Object
    
    if (Compare-Object $expectedParents $actualParents -PassThru | Measure-Object).Count -eq 0 {
        Write-Host "[OK] Режим замены работает корректно: все старые родители заменены новыми" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Ожидалось: $($expectedParents -join ', ')" -ForegroundColor Red
        Write-Host "  Получено: $($actualParents -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Ошибка при выполнении запроса: $_" -ForegroundColor Red
    $_.Exception.Response | Format-List -Force
}

Write-Host ""

# Тест 3: Проверка через эндпойнт /move
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Тест 3: Режимы через эндпойнт /move" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Используем другой курс для теста /move
$testCourseId = $courseWithoutParent1

# Тест 3.1: Добавление через /move
Write-Host "[STEP 3.1] Добавление родителя через /move (replace_parents=false)..." -ForegroundColor Yellow
$body3_1 = @{
    new_parent_ids = @($courseWithoutParent2)
    replace_parents = $false
} | ConvertTo-Json

try {
    $response3_1 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId/move?api_key=$apiKey" -Method PATCH -Body $body3_1 -ContentType "application/json"
    Write-Host "[OK] Запрос выполнен успешно" -ForegroundColor Green
    
    $check3_1 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId?api_key=$apiKey" -Method GET
    $parentIds3_1 = $check3_1.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Родители после добавления: $($parentIds3_1 -join ', ')" -ForegroundColor Gray
    
    if ($parentIds3_1 -contains $courseWithoutParent2) {
        Write-Host "[OK] Добавление через /move работает корректно" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Родитель не добавлен" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Ошибка: $_" -ForegroundColor Red
}

Write-Host ""

# Тест 3.2: Замена через /move
Write-Host "[STEP 3.2] Замена родителей через /move (replace_parents=true)..." -ForegroundColor Yellow
$body3_2 = @{
    new_parent_ids = @($courseWithoutParent3)
    replace_parents = $true
} | ConvertTo-Json

try {
    $response3_2 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId/move?api_key=$apiKey" -Method PATCH -Body $body3_2 -ContentType "application/json"
    Write-Host "[OK] Запрос выполнен успешно" -ForegroundColor Green
    
    $check3_2 = Invoke-RestMethod -Uri "$baseUrl/courses/$testCourseId?api_key=$apiKey" -Method GET
    $parentIds3_2 = $check3_2.parent_courses | ForEach-Object { $_.id }
    Write-Host "  Родители после замены: $($parentIds3_2 -join ', ')" -ForegroundColor Gray
    
    if ($parentIds3_2.Count -eq 1 -and $parentIds3_2[0] -eq $courseWithoutParent3) {
        Write-Host "[OK] Замена через /move работает корректно" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Ожидался только родитель $courseWithoutParent3, получено: $($parentIds3_2 -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "[ERROR] Ошибка: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Проверка логов и БД" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Проверка логов
Write-Host "[INFO] Проверка логов..." -ForegroundColor Yellow
if (Test-Path "logs/app.log") {
    $logContent = Get-Content "logs/app.log" -Tail 50 -Encoding UTF8
    $errorLines = $logContent | Select-String -Pattern "ERROR|Exception|Traceback" -Context 2
    if ($errorLines) {
        Write-Host "[WARN] Найдены ошибки в логах:" -ForegroundColor Yellow
        $errorLines | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    } else {
        Write-Host "[OK] Критических ошибок в логах не найдено" -ForegroundColor Green
    }
} else {
    Write-Host "[WARN] Файл логов не найден" -ForegroundColor Yellow
}

# Проверка БД через Python
Write-Host ""
Write-Host "[INFO] Проверка состояния БД..." -ForegroundColor Yellow
$dbCheckResult = python tests\check_db_state_for_replace_test.py $courseWithParent $testCourseId 2>&1
Write-Host $dbCheckResult

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Тесты завершены" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
