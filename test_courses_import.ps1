# Тестирование импорта курсов из Google Sheets

$ErrorActionPreference = "Stop"

# Цвета для вывода
function Write-Success { param($msg) Write-Host $msg -ForegroundColor Green }
function Write-Error { param($msg) Write-Host $msg -ForegroundColor Red }
function Write-Info { param($msg) Write-Host $msg -ForegroundColor Cyan }
function Write-Warning { param($msg) Write-Host $msg -ForegroundColor Yellow }

# Загружаем API ключ
$env:API_KEY = (Get-Content .env | Select-String "VALID_API_KEYS" | ForEach-Object { ($_ -split "=")[1] -split "," | Select-Object -First 1 }).Trim()
if (-not $env:API_KEY) {
    Write-Error "API Key не найден в .env файле"
    exit 1
}

$baseUrl = "http://localhost:8000/api/v1"
$spreadsheetUrl = "https://docs.google.com/spreadsheets/d/185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc/edit?usp=sharing"

Write-Host "============================================================" -ForegroundColor Green
Write-Host "Тестирование импорта курсов из Google Sheets" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Info "Spreadsheet URL: $spreadsheetUrl"
Write-Info "Spreadsheet ID: 185yB39jP8IF_SGJTpWMRXPHYYXF6FZz6Pji70O8Krhc"
Write-Host ""

# Функция для выполнения API запроса
function Invoke-ApiRequest {
    param(
        [string]$Method,
        [string]$Endpoint,
        [object]$Body = $null
    )
    
    # Добавляем API ключ в query параметры
    $separator = if ($Endpoint -match '\?') { "&" } else { "?" }
    $endpointWithKey = "$Endpoint$separator" + "api_key=$env:API_KEY"
    
    $headers = @{
        "Content-Type" = "application/json"
    }
    
    try {
        if ($Body) {
            $jsonBody = $Body | ConvertTo-Json -Depth 10 -Compress
            $response = Invoke-RestMethod -Uri "$baseUrl$endpointWithKey" -Method $Method -Headers $headers -Body $jsonBody -ErrorAction Stop
        } else {
            $response = Invoke-RestMethod -Uri "$baseUrl$endpointWithKey" -Method $Method -Headers $headers -ErrorAction Stop
        }
        return @{ Success = $true; Data = $response }
    } catch {
        $statusCode = $null
        $errorBody = $null
        if ($_.Exception.Response) {
            $statusCode = $_.Exception.Response.StatusCode.value__
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $responseBody = $reader.ReadToEnd()
                $errorBody = $responseBody | ConvertFrom-Json -ErrorAction SilentlyContinue
            } catch {
                $errorBody = @{ detail = $_.Exception.Message }
            }
        } else {
            $errorBody = @{ detail = $_.Exception.Message }
        }
        return @{ Success = $false; StatusCode = $statusCode; Error = $errorBody; Exception = $_.Exception.Message }
    }
}

# Тест 1: Dry Run
Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Тест 1: Dry Run (проверка без сохранения)" -ForegroundColor Cyan
Write-Host "------------------------------------------------------------" -ForegroundColor Cyan

$dryRunBody = @{
    spreadsheet_url = $spreadsheetUrl
    sheet_name = "Courses"
    dry_run = $true
}

$dryRunResult = Invoke-ApiRequest -Method "POST" -Endpoint "/courses/import/google-sheets" -Body $dryRunBody

if ($dryRunResult.Success) {
    Write-Success "✓ Dry Run успешно выполнен"
    Write-Host "  Imported: $($dryRunResult.Data.imported)"
    Write-Host "  Updated: $($dryRunResult.Data.updated)"
    Write-Host "  Total Rows: $($dryRunResult.Data.total_rows)"
    Write-Host "  Errors: $($dryRunResult.Data.errors.Count)"
    
    if ($dryRunResult.Data.errors.Count -gt 0) {
        Write-Warning "  Обнаружены ошибки:"
        foreach ($error in $dryRunResult.Data.errors) {
            Write-Warning "    Строка $($error.row_index): $($error.error)"
        }
    }
} else {
    Write-Error "✗ Dry Run завершился с ошибкой"
    Write-Host "  Status Code: $($dryRunResult.StatusCode)"
    Write-Host "  Error: $($dryRunResult.Error.detail)"
    Write-Host "  Exception: $($dryRunResult.Exception)"
    exit 1
}

Write-Host ""

# Тест 2: Реальный импорт
Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Тест 2: Реальный импорт" -ForegroundColor Cyan
Write-Host "------------------------------------------------------------" -ForegroundColor Cyan

$importBody = @{
    spreadsheet_url = $spreadsheetUrl
    sheet_name = "Courses"
    dry_run = $false
}

$importResult = Invoke-ApiRequest -Method "POST" -Endpoint "/courses/import/google-sheets" -Body $importBody

if ($importResult.Success) {
    Write-Success "✓ Импорт успешно выполнен"
    Write-Host "  Imported: $($importResult.Data.imported)"
    Write-Host "  Updated: $($importResult.Data.updated)"
    Write-Host "  Total Rows: $($importResult.Data.total_rows)"
    Write-Host "  Errors: $($importResult.Data.errors.Count)"
    
    if ($importResult.Data.errors.Count -gt 0) {
        Write-Warning "  Обнаружены ошибки:"
        foreach ($error in $importResult.Data.errors) {
            Write-Warning "    Строка $($error.row_index) (course_uid: $($error.course_uid)): $($error.error)"
        }
    }
} else {
    Write-Error "✗ Импорт завершился с ошибкой"
    Write-Host "  Status Code: $($importResult.StatusCode)"
    Write-Host "  Error: $($importResult.Error.detail)"
    Write-Host "  Exception: $($importResult.Exception)"
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Тесты импорта завершены!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
