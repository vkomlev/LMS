# Test materials import from Google Sheets: dry_run + live
# URL: https://docs.google.com/spreadsheets/d/16xdksyZnll09VQ5tGnwSEFCtiK4f9wF2tTxW14GKZjA/edit?usp=sharing
# Validate: logs/app.log + DB via MCP

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ApiKey,
    [string]$SpreadsheetUrl = "https://docs.google.com/spreadsheets/d/16xdksyZnll09VQ5tGnwSEFCtiK4f9wF2tTxW14GKZjA/edit?usp=sharing",
    [string]$SheetName = "Materials"
)

$ErrorActionPreference = "Stop"
$API = "/api/v1/"

if (-not $ApiKey) {
    $envFile = Join-Path $PSScriptRoot "..\\.env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "VALID_API_KEYS" } | Select-Object -First 1
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
function Post-Req { param([string]$Path, [object]$Body) Invoke-RestMethod -Uri (Url $Path) -Method POST -Body ($Body | ConvertTo-Json -Compress) -ContentType "application/json; charset=utf-8" }

function Pass { Write-Host "[PASS] $args" -ForegroundColor Green }
function Fail { Write-Host "[FAIL] $args" -ForegroundColor Red }
function Info { Write-Host "[INFO] $args" -ForegroundColor Cyan }

Write-Host "`n=== Materials import: DRY + Live ===`n" -ForegroundColor Cyan

# 1) DRY RUN
$dryBody = @{
    spreadsheet_url = $SpreadsheetUrl
    sheet_name = $SheetName
    dry_run = $true
}
try {
    $dryR = Post-Req "/materials/import/google-sheets" $dryBody
    Write-Host "DRY RUN: total_rows=$($dryR.total_rows), imported=$($dryR.imported), updated=$($dryR.updated), errors=$($dryR.errors.Count)" -ForegroundColor Yellow
    if ($dryR.errors -and $dryR.errors.Count -gt 0) {
        foreach ($e in $dryR.errors) { Write-Host "  error row $($e.row): $($e.error)" -ForegroundColor Gray }
    }
    if ($dryR.by_course) {
        foreach ($c in $dryR.by_course) { Write-Host "  course $($c.course_uid) id=$($c.course_id): imported=$($c.imported), errors=$($c.errors.Count)" -ForegroundColor Gray }
    }
    Pass "DRY RUN completed"
} catch {
    Fail "DRY RUN: $_"
}

# 2) LIVE (dry_run=false)
$liveBody = @{
    spreadsheet_url = $SpreadsheetUrl
    sheet_name = $SheetName
    dry_run = $false
}
try {
    $liveR = Post-Req "/materials/import/google-sheets" $liveBody
    Write-Host "LIVE: total_rows=$($liveR.total_rows), imported=$($liveR.imported), updated=$($liveR.updated), errors=$($liveR.errors.Count)" -ForegroundColor Yellow
    if ($liveR.errors -and $liveR.errors.Count -gt 0) {
        foreach ($e in $liveR.errors) { Write-Host "  error row $($e.row): $($e.error)" -ForegroundColor Gray }
    }
    if ($liveR.by_course) {
        foreach ($c in $liveR.by_course) { Write-Host "  course $($c.course_uid) id=$($c.course_id): imported=$($c.imported), updated=$($c.updated), errors=$($c.errors.Count)" -ForegroundColor Gray }
    }
    Pass "LIVE import completed"
} catch {
    Fail "LIVE import: $_"
}

Write-Host "`n=== Check logs/app.log and DB (materials) for validation ===`n" -ForegroundColor Cyan
