# Real-time upload monitor for PR #73 performance testing
# Monitors Azure logs and reports batch performance

Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host ("=" * 79) -ForegroundColor Cyan
Write-Host "UPLOAD MONITOR - PR #73 Performance Test" -ForegroundColor Cyan
Write-Host ("=" * 80) -ForegroundColor Cyan
Write-Host ""
Write-Host "✅ Deployment: COMPLETED (10:21 AM IST)" -ForegroundColor Green
Write-Host "✅ Fixes: gc.collect() removed + frontend 404 handling" -ForegroundColor Green
Write-Host ""
Write-Host "Expected Performance:" -ForegroundColor Yellow
Write-Host "  - Processing: 10-15 min (vs 40 min before)" -ForegroundColor White
Write-Host "  - Batch speed: 25-30 sec (vs 600 sec before)" -ForegroundColor White
Write-Host "  - No swap thrashing (30+ pairs/s vs 1 pairs/s)" -ForegroundColor White
Write-Host ""
Write-Host "Files to test:" -ForegroundColor Yellow
Write-Host "  1. tickertape_customer_feedback (1).csv - 200 comments" -ForegroundColor White
Write-Host "  2. Booktask (1).csv - Large file stress test" -ForegroundColor White
Write-Host ""
Write-Host ("=" * 80) -ForegroundColor Cyan
Write-Host ""
Write-Host "👉 Upload a file now via the UI, I'll track it automatically!" -ForegroundColor Green
Write-Host ""

$TrackedTasks = @{}
$LastLogTime = Get-Date

while ($true) {
    try {
        # Download logs
        $Timestamp = Get-Date -Format "HHmmss"
        $TempZip = "$env:TEMP\monitor-$Timestamp.zip"
        $TempDir = "$env:TEMP\monitor-$Timestamp"

        az webapp log download --name saramsa-celery-prod-2 --resource-group saramsa `
            --log-file $TempZip 2>&1 | Out-Null

        if (Test-Path $TempZip) {
            Expand-Archive -Path $TempZip -DestinationPath $TempDir -Force 2>$null

            $LogFile = Get-ChildItem "$TempDir\LogFiles\*_default_docker.log" -ErrorAction SilentlyContinue |
                Select-Object -First 1

            if ($LogFile) {
                $LogContent = Get-Content $LogFile.FullName -Tail 2000 -ErrorAction SilentlyContinue

                # Find new tasks
                $TaskStarts = $LogContent | Select-String "Background task started.*task_id=([a-f0-9-]+)" |
                    ForEach-Object {
                        $TaskId = $_.Matches.Groups[1].Value
                        $Line = $_.Line
                        if ($Line -match '(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})') {
                            $Time = $matches[1]
                        }
                        if ($Line -match 'n_items=(\d+)') {
                            $Comments = $matches[1]
                        }
                        @{TaskId=$TaskId; Time=$Time; Comments=$Comments}
                    }

                foreach ($Task in $TaskStarts) {
                    if (-not $TrackedTasks.ContainsKey($Task.TaskId)) {
                        Write-Host ""
                        Write-Host ("=" * 80) -ForegroundColor Cyan
                        Write-Host "🆕 NEW UPLOAD DETECTED!" -ForegroundColor Green
                        Write-Host ("=" * 80) -ForegroundColor Cyan
                        Write-Host "Task ID:  $($Task.TaskId)" -ForegroundColor White
                        Write-Host "Started:  $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor White
                        Write-Host "Comments: $($Task.Comments)" -ForegroundColor White
                        Write-Host ""

                        $TrackedTasks[$Task.TaskId] = @{
                            StartTime = Get-Date
                            Comments = $Task.Comments
                            LastBatch = 0
                        }
                    }
                }

                # Monitor active tasks
                foreach ($TaskId in $TrackedTasks.Keys) {
                    $TaskInfo = $TrackedTasks[$TaskId]

                    # Find batch progress
                    $BatchLines = $LogContent | Select-String "DIAG.*Batch.*$TaskId"

                    if ($BatchLines) {
                        $LatestBatch = $BatchLines | Select-Object -Last 1

                        if ($LatestBatch -match 'Batch (\d+)/(\d+).*in ([\d.]+)s.*\((\d+) pairs/s\)') {
                            $Current = [int]$matches[1]
                            $Total = [int]$matches[2]
                            $Time = [float]$matches[3]
                            $Rate = [int]$matches[4]

                            if ($Current -gt $TaskInfo.LastBatch) {
                                $TaskInfo.LastBatch = $Current
                                $Pct = [int](($Current / $Total) * 100)
                                $Elapsed = ((Get-Date) - $TaskInfo.StartTime).TotalSeconds

                                $Color = if ($Rate -ge 20) { "Green" } elseif ($Rate -ge 10) { "Yellow" } else { "Red" }

                                Write-Host ("  [{0,3}s] Batch {1,2}/{2} | {3,5:F1}s | {4,3} pairs/s | {5,3}% complete" -f `
                                    [int]$Elapsed, $Current, $Total, $Time, $Rate, $Pct) -ForegroundColor $Color
                            }
                        }
                    }

                    # Check for completion
                    $Success = $LogContent | Select-String "PHASE END.*$TaskId|SUCCESS.*$TaskId"

                    if ($Success) {
                        $Elapsed = ((Get-Date) - $TaskInfo.StartTime).TotalSeconds
                        $Minutes = [int]($Elapsed / 60)
                        $Seconds = [int]($Elapsed % 60)

                        Write-Host ""
                        Write-Host ("=" * 80) -ForegroundColor Green
                        Write-Host "✅ TASK COMPLETED!" -ForegroundColor Green
                        Write-Host ("=" * 80) -ForegroundColor Green
                        Write-Host "Time:     ${Minutes}m ${Seconds}s (${Elapsed}s total)" -ForegroundColor White
                        Write-Host "Comments: $($TaskInfo.Comments)" -ForegroundColor White
                        Write-Host ""

                        # Performance verdict
                        if ($Elapsed -le 900) {  # 15 minutes
                            Write-Host "🚀 EXCELLENT! Within expected 10-15 min range" -ForegroundColor Green
                        } elseif ($Elapsed -le 1200) {  # 20 minutes
                            Write-Host "✅ GOOD! Much better than 40 min before fix" -ForegroundColor Yellow
                        } else {
                            Write-Host "⚠️  SLOW - May still have issues" -ForegroundColor Red
                        }

                        Write-Host ""
                        Write-Host ("=" * 80) -ForegroundColor Cyan
                        Write-Host ""

                        $TrackedTasks.Remove($TaskId)
                    }
                }
            }

            # Cleanup
            Remove-Item $TempZip -ErrorAction SilentlyContinue
            Remove-Item $TempDir -Recurse -ErrorAction SilentlyContinue
        }
    }
    catch {
        Write-Host "⚠️  Error: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 10
}
