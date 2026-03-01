# Smoke: Learning Engine V1, этап 3.9 — Teacher Next Modes (claim-next, release, workload)
# Параметры: API_KEY, TEACHER_ID; опционально: HOST
# Пример: $env:API_KEY='key'; $env:TEACHER_ID=1; .\scripts\smoke_learning_engine_stage39_next_modes.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$HostUrl = if ($env:HOST) { $env:HOST } else { "http://localhost:8000" }
$Base = "$HostUrl/api/v1"
$ApiKey = $env:API_KEY
$TeacherId = $env:TEACHER_ID
$query = if ($ApiKey) { "?api_key=$ApiKey" } else { "" }

if (-not $ApiKey -or -not $TeacherId) {
    Write-Host "Задайте API_KEY и TEACHER_ID. Опционально: HOST"
    Write-Host "Пример: `$env:API_KEY='key'; `$env:TEACHER_ID=1; .\scripts\smoke_learning_engine_stage39_next_modes.ps1"
    exit 1
}

$script:allOk = $true

function Invoke-Get {
    param([string]$Uri)
    try {
        $r = Invoke-RestMethod -Uri $Uri -Method Get
        return @($true, $r)
    } catch {
        return @($false, $_.Exception.Message)
    }
}

function Invoke-Post {
    param([string]$Uri, [object]$Body = $null)
    try {
        $bodyJson = if ($Body) { $Body | ConvertTo-Json -Depth 5 } else { "{}" }
        $r = Invoke-RestMethod -Uri $Uri -Method Post -ContentType "application/json" -Body $bodyJson
        return @($true, $r)
    } catch {
        return @($false, $_.Exception.Message)
    }
}

function Step {
    param([string]$Name, [scriptblock]$Run)
    Write-Host "--- $Name ---"
    $ok, $result = & $Run
    if ($ok) {
        Write-Host "[PASS] $Name"
        return $result
    }
    Write-Host "[FAIL] $Name : $result"
    $script:allOk = $false
    return $null
}

# 1. Workload
$workload = Step {
    Invoke-Get "$Base/teacher/workload$query&teacher_id=$TeacherId"
}
if (-not $workload) { exit 1 }
if ($workload.PSObject.Properties.Name -notmatch "open_help_requests_total|pending_manual_reviews_total") {
    Write-Host "[FAIL] Workload: ожидались поля open_help_requests_total, pending_manual_reviews_total и др."
    $script:allOk = $false
}

# 2. Claim-next help-request
$claimBody = @{ teacher_id = [int]$TeacherId; request_type = "all"; ttl_sec = 120 }
$claimHelp = Step {
    Invoke-Post "$Base/teacher/help-requests/claim-next$query" $claimBody
}
if (-not $claimHelp) { exit 1 }
$lockToken = $claimHelp.lock_token
$requestId = $claimHelp.item.request_id

# 3. Release help-request (если был выдан кейс)
if ($claimHelp.empty -eq $false -and $requestId -and $lockToken) {
    $releaseBody = @{ teacher_id = [int]$TeacherId; lock_token = $lockToken }
    Step {
        Invoke-Post "$Base/teacher/help-requests/$requestId/release$query" $releaseBody
    } | Out-Null
}

# 4. Claim-next review
$claimReviewBody = @{ teacher_id = [int]$TeacherId; ttl_sec = 120 }
$claimReview = Step {
    Invoke-Post "$Base/teacher/reviews/claim-next$query" $claimReviewBody
}
if (-not $claimReview) { exit 1 }
$reviewLockToken = $claimReview.lock_token
$resultId = $claimReview.item.id

# 5. Release review (если был выдан кейс)
if ($claimReview.empty -eq $false -and $resultId -and $reviewLockToken) {
    $releaseReviewBody = @{ teacher_id = [int]$TeacherId; lock_token = $reviewLockToken }
    Step {
        Invoke-Post "$Base/teacher/reviews/$resultId/release$query" $releaseReviewBody
    } | Out-Null
}

# 6. List help-requests с sort=priority
$list = Step {
    Invoke-Get "$Base/teacher/help-requests$query&teacher_id=$TeacherId&status=open&sort=priority&limit=5"
}
if ($list -and $list.items -and $list.items.Count -gt 0) {
    $first = $list.items[0]
    if (-not ($first.PSObject.Properties.Name -match "priority|due_at|is_overdue")) {
        Write-Host "[FAIL] List: в элементах ожидались priority, due_at, is_overdue"
        $script:allOk = $false
    }
}

Write-Host ""
if ($script:allOk) {
    Write-Host "Smoke stage 3.9: все шаги PASS"
    exit 0
} else {
    Write-Host "Smoke stage 3.9: есть провалы"
    exit 1
}
