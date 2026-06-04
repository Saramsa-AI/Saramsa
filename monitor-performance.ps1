# Performance monitoring script for testing the gc.collect() fixes
# Usage: .\monitor-performance.ps1

Write-Host "==================================" -ForegroundColor Cyan
Write-Host "PERFORMANCE MONITOR - PR #73 Test" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Watching for new uploads..." -ForegroundColor Yellow
Write-Host "Expected: 10-15 min processing (vs 40 min before)" -ForegroundColor Green
Write-Host "Expected: 25-30 sec/batch (vs 600 sec before)" -ForegroundColor Green
Write-Host ""

$LastTask = ""
$StartTime = $null

while ($true) {
    # Download latest logs
    $TempZip = "$env:TEMP\perf-monitor-$(Get-Date -Format 'HHmmss').zip"
    $TempDir = "$env:TEMP\perf-monitor-$(Get-Date -Format 'HHmmss')"

    az webapp log download --name saramsa-celery-prod-2 --resource-group saramsa `
        --log-file $TempZip 2>&1 | Out-Null

    Expand-Archive -Path $TempZip -DestinationPath $TempDir -Force 2>$null

    $LogFile = Get-ChildItem "$TempDir\LogFiles\*_default_docker.log" | Select-Object -First 1

    if ($LogFile) {
        # Find latest task
        $TaskLine = Get-Content $LogFile.FullName -Tail 500 |
            Select-String "Background task started" |
            Select-Object -Last 1

        if ($TaskLine) {
            $TaskId = [regex]::Match($TaskLine, 'task_id=([a-f0-9-]+)').Groups[1].Value

            if ($TaskId -and $TaskId -ne $LastTask) {
                Write-Host ""
                Write-Host "🆕 NEW TASK: $TaskId" -ForegroundColor Cyan
                Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray
                $LastTask = $TaskId
                $StartTime = Get-Date
            }
        }

        if ($LastTask) {
            # Get latest batch info
            $BatchLines = Get-Content $LogFile.FullName -Tail 1000 |
                Select-String "DIAG.*Batch" |
                Select-Object -Last 1

            if ($BatchLines) {
                $Line = $BatchLines.Line

                # Extract info
                if ($Line -match 'Batch (\d+/\d+)') { $Batch = $matches[1] }
                if ($Line -match 'in ([\d.]+)s') { $Time = $matches[1] }
                if ($Line -match '\((\d+) pairs/s\)') { $Rate = $matches[1] }

                if ($Batch) {
                    Write-Host "📊 Batch $Batch | ${Time}s | ${Rate} pairs/s" -ForegroundColor White
                }

                # Check for completion
                if ($Line -match "PHASE END|SUCCESS") {
                    $Elapsed = (Get-Date) - $StartTime
                    Write-Host ""
                    Write-Host "✅ COMPLETED in $($Elapsed.TotalSeconds)s ($([int]$Elapsed.TotalMinutes)m $($Elapsed.Seconds)s)" -ForegroundColor Green
                    Write-Host ""
                    $LastTask = ""
                }
            }
        }
    }

    # Cleanup
    Remove-Item $TempZip -ErrorAction SilentlyContinue
    Remove-Item $TempDir -Recurse -ErrorAction SilentlyContinue

    Start-Sleep -Seconds 10
}
