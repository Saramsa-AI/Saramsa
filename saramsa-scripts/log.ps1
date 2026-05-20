# Saramsa log - show logs for one service (or all)

param(
    [Parameter(Position=0)][string]$Service = "all",
    [Parameter(Position=1)][string]$Mode = ""
)

. "$PSScriptRoot\common.ps1"

$serviceKey = $Service.ToLowerInvariant()
$follow = $Mode -eq "-f" -or $Mode -eq "--follow"

$logMap = @{
    "frontend"        = $FrontendLog
    "backend"         = $BackendDjangoLog
    "backend-honcho"  = $BackendLog
    "celery"          = $CeleryDjangoLog
    "celery-honcho"   = $CeleryLog
    "celery-ops"      = $CeleryOpsLog
    "system"          = $SystemLog
    "all-honcho"      = $AllLog
}

# 'all' is synthesized live from multiple reliable sources rather than the
# honcho-fanned .saramsa-all.log (which goes silent when honcho's stdout
# pipe to a child breaks — common with Django on Windows). 'all-honcho'
# is kept as an escape hatch for the original combined-prefix view.
if ($serviceKey -eq "all") {
    $sources = [ordered]@{
        "backend"    = $BackendDjangoLog
        "celery"     = $CeleryDjangoLog
        "frontend"   = $FrontendLog
        "celery-ops" = $CeleryOpsLog
        "system"     = $SystemLog
    }
    $existing = [ordered]@{}
    foreach ($key in $sources.Keys) {
        if (Test-Path $sources[$key]) { $existing[$key] = $sources[$key] }
    }
    if ($existing.Count -eq 0) {
        Write-Host "[ERROR] No log files exist yet. Run 'saramsa start' first." -ForegroundColor Red
        exit 1
    }
    # OrderedDictionary uses .Contains, not .ContainsKey
    $missing = $sources.Keys | Where-Object { -not $existing.Contains($_) }
    if ($missing) {
        Write-Host ("[INFO] Skipping missing sources: " + ($missing -join ", ")) -ForegroundColor Yellow
    }
    Write-Host ("Following: " + ($existing.Keys -join ", ")) -ForegroundColor Cyan
    Watch-MultiLogs -Sources $existing -InitialTailLines 20 -Follow:$follow
    return
}

if (-not $logMap.ContainsKey($serviceKey)) {
    Write-Host "[ERROR] Unknown log target: $Service" -ForegroundColor Red
    Write-Host "Use one of: frontend, backend, backend-honcho, celery, celery-honcho, celery-ops, system, all, all-honcho" -ForegroundColor Yellow
    exit 1
}

$target = $logMap[$serviceKey]
if (-not (Test-Path $target)) {
    Write-Host "[ERROR] Log file not found yet: $target" -ForegroundColor Red
    if ($serviceKey -in @("backend","celery")) {
        Write-Host "These follow Django's direct logs at backend\logs\. Start the stack and send one request to create the file." -ForegroundColor Yellow
    } else {
        Write-Host "Run 'saramsa start' first to generate logs." -ForegroundColor Yellow
    }
    exit 1
}

Write-Host "Reading $serviceKey logs from: $target" -ForegroundColor Cyan
if ($follow) {
    Get-Content -Path $target -Tail 120 -Wait
} else {
    Get-Content -Path $target -Tail 120
}
