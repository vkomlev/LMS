# Smoke tests for Materials API endpoints
# Проверка: CURL-запросы к API; валидация по логам logs/app.log (операции api.materials_extra, включая search и upload);
# состояние БД проверять через MCP (materials для course_id 7, 8).
# Перед тестами: взять реальные course_id из БД через MCP (query: SELECT id FROM courses WHERE course_uid IN ('COURSE-PY-01','COURSE-MATH-01')).
# Если MCP недоступен: запустить scripts/connect_db.py для проверки подключения к БД.
# Ожидаемое состояние БД после прогона: course_id=7 — 0 материалов; course_id=8 — 1 материал (копия, is_active=false).
# Требуется: запущенный API (run.py), сеть. Файл для upload: tests/fixtures/smoke_upload.txt.

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ApiKey,
    [string]$LogFile = "logs/app.log",
    [switch]$SkipGoogleSheetsImport
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

function Url { param([string]$Path, [hashtable]$Q = @{}) 
    $q["api_key"] = $ApiKey
    $qstr = ($Q.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join "&"
    $pathPart = ($API.TrimEnd("/") + "/" + $Path.TrimStart("/")).TrimEnd("/")
    if ($pathPart -eq "") { $pathPart = $API.TrimEnd("/") }
    $sep = if ($Path -match "\?") { "&" } else { "?" }
    "$BaseUrl$pathPart$sep$qstr"
}
function Get-Req { param([string]$Path, [hashtable]$Q = @{}) Invoke-RestMethod -Uri (Url $Path $Q) -Method GET }
function Post-Req { param([string]$Path, [object]$Body) Invoke-RestMethod -Uri (Url $Path) -Method POST -Body ($Body | ConvertTo-Json -Compress) -ContentType "application/json; charset=utf-8" }
function Patch-Req { param([string]$Path, [object]$Body) Invoke-RestMethod -Uri (Url $Path) -Method PATCH -Body ($Body | ConvertTo-Json -Compress) -ContentType "application/json; charset=utf-8" }
function Delete-Req { param([string]$Path) Invoke-RestMethod -Uri (Url $Path) -Method DELETE }

function Pass { Write-Host "[PASS] $args" -ForegroundColor Green }
function Fail { Write-Host "[FAIL] $args" -ForegroundColor Red }
function Info { Write-Host "[INFO] $args" -ForegroundColor Cyan }

# Курс с course_uid для импорта и для тестов (COURSE-PY-01)
$CourseId = 7
$CourseId2 = 8

Write-Host "`n=== Materials API Smoke Tests (api_key=***) ===`n" -ForegroundColor Cyan
$firstPath = ($API.TrimEnd("/") + "/" + ("/courses/$CourseId/materials").TrimStart("/"))
$firstUrl = $BaseUrl + $firstPath + "?api_key=***"
Write-Host "[INFO] First request URL: $firstUrl" -ForegroundColor Gray

# 1) GET /courses/{course_id}/materials — список материалов курса
try {
    $r = Get-Req "/courses/$CourseId/materials"
    if ($null -eq $r.items) { Fail "GET /courses/$CourseId/materials: no .items"; throw "fail" }
    Pass "GET /courses/$CourseId/materials -> items=$($r.items.Count), total=$($r.total)"
} catch { Fail "GET /courses/$CourseId/materials: $_" }

# 2) GET /materials/search — глобальный поиск (реальные данные из БД: course 7 — «Введение в Python», «MAT-PY-01»)
try {
    $searchR = Get-Req "/materials/search" -Q @{ q = "Python"; limit = 10 }
    if ($null -eq $searchR.items) { Fail "GET /materials/search: no .items"; throw "fail" }
    Pass "GET /materials/search?q=Python -> total=$($searchR.total)"
} catch { Fail "GET /materials/search: $_" }

# 3) GET /courses/{course_id}/materials?q= — поиск в рамках курса
try {
    $listQ = Get-Req "/courses/$CourseId/materials" -Q @{ q = "Python"; limit = 10 }
    if ($null -eq $listQ.items) { Fail "GET /courses/.../materials?q=: no .items"; throw "fail" }
    Pass "GET /courses/$CourseId/materials?q=Python -> total=$($listQ.total)"
} catch { Fail "GET /courses/.../materials?q=: $_" }

# 4) POST /materials — создание материала (order_position не передаём — триггер поставит в конец)
$createBody = @{
    course_id = $CourseId
    title = "Smoke test material (link)"
    type = "link"
    content = @{ url = "https://example.com/smoke"; title = "Example" }
    description = $null
    caption = $null
    order_position = $null
    is_active = $true
    external_uid = "SMOKE-MAT-01"
}
try {
    $created = Post-Req "/materials" $createBody
    $mid = $created.id
    if (-not $mid) { Fail "POST /materials: no id in response"; throw "fail" }
    Pass "POST /materials -> id=$mid, order_position=$($created.order_position)"
} catch { Fail "POST /materials: $_" }

# 6) POST /materials/upload — загрузка файла (текстовый tests/fixtures/smoke_upload.txt)
$fileId = $null
$uploadFilePath = Join-Path $PSScriptRoot "fixtures\smoke_upload.txt"
if (-not (Test-Path $uploadFilePath)) {
    Fail "Fixture not found: $uploadFilePath"
} else {
    try {
        $uploadUri = Url "/materials/upload"
        $uploadResp = Invoke-RestMethod -Uri $uploadUri -Method POST -Form @{ file = Get-Item -Path $uploadFilePath }
        if (-not $uploadResp.url) { Fail "POST /materials/upload: no url in response"; throw "fail" }
        $fileId = Split-Path $uploadResp.url -Leaf
        Pass "POST /materials/upload -> url=$($uploadResp.url), filename=$($uploadResp.filename)"
    } catch { Fail "POST /materials/upload: $_" }
}

# 7) GET /materials/files/{file_id} — скачивание загруженного файла
if ($fileId) {
    try {
        $fileContent = Invoke-WebRequest -Uri (Url "/materials/files/$fileId") -Method GET -UseBasicParsing
        if ($fileContent.StatusCode -ne 200) { Fail "GET /materials/files: status $($fileContent.StatusCode)"; throw "fail" }
        if (-not $fileContent.Content) { Fail "GET /materials/files: empty body"; throw "fail" }
        Pass "GET /materials/files/$fileId -> status=200, length=$($fileContent.Content.Length)"
    } catch { Fail "GET /materials/files/$fileId: $_" }
}

# 8) GET /materials/{id}
try {
    $one = Get-Req "/materials/$mid"
    if ($one.title -ne $createBody.title) { Fail "GET /materials/$($mid): title mismatch"; throw "fail" }
    Pass "GET /materials/$($mid) -> title=$($one.title)"
} catch { Fail "GET /materials/$($mid): $_" }

# 9) PATCH /materials/{id}
try {
    $upd = Patch-Req "/materials/$mid" @{ title = "Smoke test material (updated)" }
    if ($upd.title -ne "Smoke test material (updated)") { Fail "PATCH: title not updated"; throw "fail" }
    Pass "PATCH /materials/$($mid) -> title updated"
} catch { Fail "PATCH /materials/$($mid): $_" }

# 10) GET /courses/{course_id}/materials — снова список (должен быть новый материал)
try {
    $list2 = Get-Req "/courses/$CourseId/materials"
    $found = $list2.items | Where-Object { $_.id -eq $mid }
    if (-not $found) { Fail "New material $($mid) not in list"; throw "fail" }
    Pass "GET /courses/$CourseId/materials after create -> found id=$($mid)"
} catch { Fail "GET list after create: $_" }

# 11) POST /courses/{course_id}/materials/reorder
$listForReorder = Get-Req "/courses/$CourseId/materials"
$orders = @()
$pos = 1
foreach ($m in $listForReorder.items) {
    $orders += @{ material_id = $m.id; order_position = $pos }
    $pos++
}
try {
    $reorderR = Post-Req "/courses/$CourseId/materials/reorder" @{ material_orders = $orders }
    if ($reorderR.updated -ge 0) { Pass "POST reorder -> updated=$($reorderR.updated)" } else { Fail "reorder: no updated" }
} catch { Fail "POST reorder: $_" }

# 12) POST /materials/{id}/move — в ту же позицию в том же курсе
try {
    $moveR = Post-Req "/materials/$mid/move" @{ new_order_position = 1; course_id = $null }
    if ($moveR.id -eq $mid) { Pass "POST /materials/$($mid)/move -> ok" } else { Fail "move: wrong id" }
} catch { Fail "POST move: $_" }

# 13) POST /courses/{course_id}/materials/bulk-update
try {
    $bulkR = Post-Req "/courses/$CourseId/materials/bulk-update" @{ material_ids = @($mid); is_active = $false }
    Pass "POST bulk-update -> updated=$($bulkR.updated)"
} catch { Fail "POST bulk-update: $_" }

# 14) POST /materials/{id}/copy — копирование в другой курс
try {
    $copyR = Post-Req "/materials/$mid/copy" @{ target_course_id = $CourseId2; order_position = $null }
    $copyId = $copyR.id
    if ($copyId -and $copyId -ne $mid) { Pass "POST copy -> new id=$($copyId)" } else { Fail "copy: no new id" }
} catch { Fail "POST copy: $_" }

# 15) GET /courses/{course_id}/materials/stats
try {
    $stats = Get-Req "/courses/$CourseId/materials/stats"
    if ($null -ne $stats.total) { Pass "GET stats -> total=$($stats.total), active=$($stats.active)" } else { Fail "stats: no total" }
} catch { Fail "GET stats: $_" }

# 16) DELETE созданного материала (оставим копию в course_id=8)
try {
    Delete-Req "/materials/$($mid)"
    Pass "DELETE /materials/$($mid)"
} catch { Fail "DELETE /materials/$($mid): $_" }

# 17) Импорт из Google Sheets — пропускаем, пока таблица не подготовлена (-SkipGoogleSheetsImport)
if (-not $SkipGoogleSheetsImport) {
    try {
        $importBody = @{
            spreadsheet_url = "invalid-url-no-id"
            sheet_name = "Materials"
            dry_run = $true
        }
        $importR = Post-Req "/materials/import/google-sheets" $importBody
        Pass "POST import/google-sheets (dry_run) -> imported=$($importR.imported), errors=$($importR.errors.Count)"
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -in 400, 404, 500) { Pass "POST import (invalid URL) -> expected error: $($_.Exception.Response.StatusCode)" }
        else { Fail "POST import: $_" }
    }
} else {
    Info "POST import/google-sheets skipped. Table to be prepared later."
}

# Валидация по логам: в logs/app.log должны быть операции api.materials_extra (включая search и upload)
$logPath = Join-Path (Split-Path $PSScriptRoot -Parent) $LogFile
$expectedOps = @("list_course_materials", "search_materials", "reorder_materials", "move_material", "bulk_update_materials", "copy_material", "get_course_materials_stats", "upload_material_file")
if (Test-Path $logPath) {
    $logContent = Get-Content $logPath -Tail 500 -Encoding UTF8 -ErrorAction SilentlyContinue
    $found = 0
    $missing = @()
    foreach ($key in $expectedOps) {
        if ($logContent -match [regex]::Escape($key)) { $found++ } else { $missing += $key }
    }
    if ($found -eq $expectedOps.Count) {
        Pass "Log validation: all $($expectedOps.Count) api.materials_extra operations found in $LogFile"
    } else {
        Fail "Log validation: found $found of $($expectedOps.Count); missing: $($missing -join ', ')"
    }
} else {
    Fail "Log validation: file not found: $logPath"
}

Write-Host "`n=== Smoke tests completed. Validate: logs=$LogFile, DB via MCP for course_id 7 and 8 ===`n" -ForegroundColor Cyan
