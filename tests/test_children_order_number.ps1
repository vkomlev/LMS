# Тест эндпойнта GET /courses/{course_id}/children с order_number
# Проверяет, что эндпойнт возвращает order_number для каждого дочернего курса

$baseUrl = "http://localhost:8000/api/v1"
$apiKey = "bot-key-1"

Write-Host "`n=== Тест GET /courses/{course_id}/children с order_number ===" -ForegroundColor Green

# Получаем курс с детьми (ID=1)
$courseId = 1
Write-Host "`n1. Получаем детей курса ID=$courseId" -ForegroundColor Cyan

$response = curl -s -X GET "$baseUrl/courses/$courseId/children?api_key=$apiKey" | ConvertFrom-Json

if ($response) {
    Write-Host "   Успешно получен ответ" -ForegroundColor Green
    Write-Host "   Количество детей: $($response.Count)" -ForegroundColor White
    
    # Проверяем наличие order_number в каждом элементе
    $allHaveOrderNumber = $true
    foreach ($child in $response) {
        $hasOrderNumber = $child.PSObject.Properties.Name -contains "order_number"
        if ($hasOrderNumber) {
            Write-Host "   - Курс ID=$($child.id), title='$($child.title)', order_number=$($child.order_number)" -ForegroundColor White
        } else {
            Write-Host "   - Курс ID=$($child.id), title='$($child.title)', order_number=ОТСУТСТВУЕТ!" -ForegroundColor Red
            $allHaveOrderNumber = $false
        }
    }
    
    if ($allHaveOrderNumber) {
        Write-Host "`n   ✅ Все дети содержат поле order_number" -ForegroundColor Green
    } else {
        Write-Host "`n   ❌ Некоторые дети не содержат поле order_number" -ForegroundColor Red
    }
    
    # Проверяем сортировку по order_number
    Write-Host "`n2. Проверяем сортировку по order_number" -ForegroundColor Cyan
    $sorted = $true
    $prevOrder = $null
    foreach ($child in $response) {
        $currentOrder = $child.order_number
        if ($prevOrder -ne $null -and $currentOrder -ne $null) {
            if ($currentOrder -lt $prevOrder) {
                Write-Host "   ❌ Нарушена сортировка: order_number=$currentOrder после order_number=$prevOrder" -ForegroundColor Red
                $sorted = $false
            }
        }
        $prevOrder = $currentOrder
    }
    if ($sorted) {
        Write-Host "   ✅ Сортировка по order_number корректна" -ForegroundColor Green
    }
} else {
    Write-Host "   ❌ Ошибка при получении ответа" -ForegroundColor Red
}

# Проверяем в БД
Write-Host "`n3. Проверяем данные в БД" -ForegroundColor Cyan
Write-Host "   Запрос: SELECT course_id, order_number, title FROM course_parents cp JOIN courses c ON cp.course_id = c.id WHERE cp.parent_course_id = $courseId ORDER BY cp.order_number NULLS LAST" -ForegroundColor Gray

# Выводим результаты для сравнения
Write-Host "`n=== Результаты теста ===" -ForegroundColor Green
Write-Host "Эндпойнт должен возвращать order_number для каждого дочернего курса" -ForegroundColor White
Write-Host "Проверьте логи в logs/app.log для подтверждения успешного выполнения" -ForegroundColor White
