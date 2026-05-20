# Saramsa scripts - shared paths and helpers
# Dot-source from start-procfile.ps1

$ErrorActionPreference = "Stop"

# Project root = parent of saramsa-scripts
$ScriptDir = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ScriptDir "backend"
$FrontendDir = Join-Path $ScriptDir "saramsa-ai"
$CeleryOpsUIDir = Join-Path $ScriptDir "celery_ops" | Join-Path -ChildPath "celery_ops" | Join-Path -ChildPath "ui" | Join-Path -ChildPath "react"
$SaramsaLogRoot = Join-Path $ScriptDir "saramsa-logs"
$RuntimeLogDir = Join-Path $SaramsaLogRoot "runtime"
New-Item -ItemType Directory -Path $RuntimeLogDir -Force | Out-Null

# Canonical runtime metadata/log paths.
$PidFile = [System.IO.Path]::GetFullPath((Join-Path $RuntimeLogDir ".saramsa-pids.json"))
$FrontendLog = Join-Path $RuntimeLogDir ".saramsa-frontend.log"
$BackendLog = Join-Path $RuntimeLogDir ".saramsa-backend.log"
$SystemLog = Join-Path $RuntimeLogDir ".saramsa-system.log"
$AllLog = Join-Path $RuntimeLogDir ".saramsa-all.log"
$BackendErrLog = Join-Path $RuntimeLogDir ".saramsa-backend.err.log"
$CeleryLog = Join-Path $RuntimeLogDir ".saramsa-celery.log"
$CeleryErrLog = Join-Path $RuntimeLogDir ".saramsa-celery.err.log"
$CeleryOpsLog = Join-Path $RuntimeLogDir ".saramsa-celery-ops.log"
$CeleryOpsErrLog = Join-Path $RuntimeLogDir ".saramsa-celery-ops.err.log"
$CeleryOpsBuildLog = Join-Path $RuntimeLogDir ".saramsa-celery-ops.build.log"
$CeleryOpsBuildErrLog = Join-Path $RuntimeLogDir ".saramsa-celery-ops.build.err.log"

# Django writes its own RotatingFileHandler logs independently of honcho's
# stdout-pipe capture. The honcho pipe occasionally goes silent on Windows
# (child stays alive but its stdout stops draining), leaving the .saramsa-*.log
# stale. These direct files are the reliable source of truth for backend
# / celery activity, so the log subcommand follows them by default.
$BackendDjangoLog = Join-Path $BackendDir "logs\api.log"
$CeleryDjangoLog  = Join-Path $BackendDir "logs\celery.log"

function Watch-MultiLogs {
    # Live-tail multiple files at once, prefixing each line with its source name.
    # Sources is an ordered hashtable name -> path (use [ordered]@{} when calling).
    # Use this to synthesize a combined view that doesn't depend on honcho's
    # stdout pipe staying alive.
    param(
        [Parameter(Mandatory=$true)]$Sources,
        [int]$InitialTailLines = 20,
        [int]$PollMs = 250,
        [switch]$Follow
    )

    $offsets = @{}
    $maxNameLen = ($Sources.Keys | Measure-Object -Maximum -Property Length).Maximum
    if (-not $maxNameLen) { $maxNameLen = 8 }

    foreach ($name in $Sources.Keys) {
        $path = $Sources[$name]
        if (Test-Path $path) {
            # UTF-8 explicit so Next.js's ✓/○ characters and other non-ASCII
            # don't render as mojibake under the Windows default (cp1252).
            $tail = Get-Content -Path $path -Tail $InitialTailLines -Encoding UTF8 -ErrorAction SilentlyContinue
            foreach ($line in $tail) {
                $tag = ("[{0}]" -f $name).PadRight($maxNameLen + 2)
                Write-Host ("{0} {1}" -f $tag, $line)
            }
            $offsets[$name] = (Get-Item $path).Length
        } else {
            $offsets[$name] = 0
        }
    }

    if (-not $Follow) { return }

    while ($true) {
        foreach ($name in $Sources.Keys) {
            $path = $Sources[$name]
            if (-not (Test-Path $path)) { continue }
            $size = (Get-Item $path).Length
            $offset = [int64]$offsets[$name]
            if ($size -lt $offset) {
                # File rotated (RotatingFileHandler) or truncated. Reset.
                $offset = 0
            }
            if ($size -le $offset) { continue }

            $fs = $null
            $sr = $null
            $newText = ""
            try {
                $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                [void]$fs.Seek($offset, [System.IO.SeekOrigin]::Begin)
                $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
                $newText = $sr.ReadToEnd()
                $offsets[$name] = $fs.Position
            } catch {
                # Skip this cycle on transient read errors; try again on the next poll.
                continue
            } finally {
                if ($sr) { $sr.Dispose() }
                if ($fs) { $fs.Dispose() }
            }

            if ([string]::IsNullOrEmpty($newText)) { continue }
            $tag = ("[{0}]" -f $name).PadRight($maxNameLen + 2)
            foreach ($line in ($newText -split "`r?`n")) {
                if ($line -ne "") { Write-Host ("{0} {1}" -f $tag, $line) }
            }
        }
        Start-Sleep -Milliseconds $PollMs
    }
}

$VenvPath = Join-Path $BackendDir "venv"
$VenvActivate = Join-Path $VenvPath "Scripts\Activate.ps1"
$UseVenv = (Test-Path $VenvActivate)

function Get-PythonExe {
    if ($UseVenv -and (Test-Path $VenvPath)) {
        $py = Join-Path $VenvPath "Scripts\python.exe"
        if (Test-Path $py) { return $py }
    }
    return "python"
}

function Rotate-Log { param([string]$Path)
    try {
        if (Test-Path $Path) {
            $dir = Split-Path -Parent $Path
            $base = [System.IO.Path]::GetFileNameWithoutExtension($Path)
            $ext = [System.IO.Path]::GetExtension($Path)
            $ts = Get-Date -Format "yyyyMMdd-HHmmss"
            $newPath = Join-Path $dir "$base.$ts$ext"
            Move-Item -Path $Path -Destination $newPath -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}

function Clear-ProcessIds {
    try { if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue } } catch { }
}

function Get-ListeningProcessId { param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($conn -and $conn.OwningProcess) { return [int]$conn.OwningProcess }
    } catch { }

    try {
        $line = netstat -ano 2>$null | Select-String "LISTENING" | Where-Object {
            $_.ToString() -match "[:.]$Port\s+.*LISTENING\s+(\d+)$"
        } | Select-Object -First 1
        if ($line -and $matches[1]) { return [int]$matches[1] }
    } catch { }

    return $null
}

function Get-ProcessCommandLine { param([int]$ProcessId)
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($proc -and $proc.CommandLine) { return [string]$proc.CommandLine }
    } catch { }
    return ""
}

function Test-SaramsaProcessForPort { param([int]$Port, [string]$CommandLine)
    if (-not $CommandLine) { return $false }
    $cmd = $CommandLine.ToLowerInvariant()

    switch ($Port) {
        8000 { return $cmd.Contains("manage.py runserver 127.0.0.1:8000") }
        3001 {
            # Next.js on Windows may show either the npm command or node start-server.js.
            return $cmd.Contains("next dev -p 3001") `
                -or $cmd.Contains("npm run dev") `
                -or ($cmd.Contains("\saramsa-ai\") -and $cmd.Contains("next\dist\server\lib\start-server.js"))
        }
        9800 { return $cmd.Contains("celery_ops serve") -and $cmd.Contains("--port 9800") }
        default { return $false }
    }
}

function Get-PortConflictSummary {
    $conflicts = @()
    foreach ($port in @(8000, 3001, 9800)) {
        $portProcessId = Get-ListeningProcessId -Port $port
        if (-not $portProcessId) { continue }
        $cmd = Get-ProcessCommandLine -ProcessId $portProcessId
        $conflicts += [pscustomobject]@{
            Port = $port
            ProcessId = $portProcessId
            IsSaramsa = (Test-SaramsaProcessForPort -Port $port -CommandLine $cmd)
            CommandLine = $cmd
        }
    }
    return $conflicts
}

function Stop-SaramsaCeleryWorkers {
    # Celery workers don't bind a port, so port-based detection misses them.
    # Without this, every saramsa kill/start cycle leaves the previous worker
    # alive — orphan workers keep consuming tasks from Redis on stale code
    # and (until the OTel connection string is disabled) keep emitting
    # 'Non-retryable server side error' lines from Azure Monitor.
    $patterns = @("celery -a apis worker", "celery -a apis beat")
    $workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            if (-not $_.CommandLine) { return $false }
            $cmd = $_.CommandLine.ToLowerInvariant()
            foreach ($p in $patterns) { if ($cmd.Contains($p)) { return $true } }
            return $false
        } |
        # Sort children-first so /T tree walks don't fight with deletions
        Sort-Object @{ Expression = "ParentProcessId"; Descending = $true }, ProcessId

    if (-not $workers -or $workers.Count -eq 0) { return }

    $killed = @{}
    foreach ($w in $workers) {
        if ($killed.ContainsKey($w.ProcessId)) { continue }
        try {
            $null = & taskkill /T /F /PID $w.ProcessId 2>$null
            $killed[$w.ProcessId] = $true
            Write-Host ("[OK] Stopped Celery worker PID " + $w.ProcessId + ".") -ForegroundColor Green
        } catch {
            Write-Host ("[ERROR] Failed to stop Celery worker PID " + $w.ProcessId + ": " + $_) -ForegroundColor Red
        }
    }
}

function Stop-SaramsaServices {
    $conflicts = Get-PortConflictSummary
    $celery = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains("celery -a apis worker") }
    if ((-not $conflicts -or $conflicts.Count -eq 0) -and (-not $celery -or $celery.Count -eq 0)) {
        Write-Host "[OK] No Saramsa services found on ports 8000, 3001, or 9800 (and no Celery workers)." -ForegroundColor Green
        Stop-SaramsaCeleryWorkers
        return
    }

    $saramsaConflicts = @($conflicts | Where-Object { $_.IsSaramsa })
    $foreignConflicts = @($conflicts | Where-Object { -not $_.IsSaramsa })

    if ($foreignConflicts.Count -gt 0) {
        foreach ($conflict in $foreignConflicts) {
            Write-Host "[WARNING] Port $($conflict.Port) is in use by PID $($conflict.ProcessId), but it does not look like a Saramsa process." -ForegroundColor Yellow
        }
    }

    if ($saramsaConflicts.Count -eq 0) {
        Write-Host "[ERROR] No Saramsa-owned processes were identified to stop." -ForegroundColor Red
        # Continue and still attempt stale Honcho cleanup below.
    }

    $stopped = @{}
    foreach ($conflict in $saramsaConflicts) {
        if ($stopped.ContainsKey($conflict.ProcessId)) { continue }
        try {
            $null = & taskkill /T /F /PID $conflict.ProcessId 2>$null
            $stopped[$conflict.ProcessId] = $true
            Write-Host "[OK] Stopped PID $($conflict.ProcessId) for port $($conflict.Port)." -ForegroundColor Green
        } catch {
            Write-Host "[ERROR] Failed to stop PID $($conflict.ProcessId) on port $($conflict.Port): $_" -ForegroundColor Red
        }
    }

    Stop-SaramsaCeleryWorkers
    Stop-StaleSaramsaHoncho
}

function Stop-StaleSaramsaHoncho {
    $scriptRootLower = $ScriptDir.ToLowerInvariant()
    $targets = @()
    try {
        $targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -eq "python.exe" -and $_.CommandLine -and
            $_.CommandLine.ToLowerInvariant().Contains($scriptRootLower) -and
            $_.CommandLine.ToLowerInvariant().Contains("-m honcho start")
        } | Select-Object -ExpandProperty ProcessId
    } catch { }

    $targets = @($targets | Sort-Object -Unique)
    foreach ($procId in $targets) {
        try {
            $null = & taskkill /T /F /PID $procId 2>$null
            Write-Host "[OK] Stopped stale Honcho PID $procId." -ForegroundColor Green
        } catch { }
    }
}

function Test-PortListening { param([string]$TargetHost = "127.0.0.1", [int]$Port = 3001, [int]$TimeoutMs = 2000)
    $tcp = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $task = $tcp.ConnectAsync($TargetHost, $Port)
        $null = $task.Wait($TimeoutMs)
        if ($task.IsCompleted -and -not $task.IsFaulted -and $tcp.Connected) { return $true }
    } catch { }
    finally { if ($tcp) { try { $tcp.Dispose() } catch {} } }
    return $false
}

function Wait-PortListening { param([int]$Port, [int]$TimeoutSec = 30, [int]$IntervalMs = 500)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (Test-PortListening -Port $Port) { return $true }
        Start-Sleep -Milliseconds $IntervalMs
    }
    return $false
}

function Check-Redis {
    try { if ((redis-cli ping 2>$null) -eq "PONG") { return $true } } catch { }
    $tcp = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $task = $tcp.ConnectAsync("127.0.0.1", 6379)
        $null = $task.Wait(2000)
        if ($task.IsCompleted -and -not $task.IsFaulted -and $tcp.Connected) { return $true }
    } catch { }
    finally { if ($tcp) { try { $tcp.Dispose() } catch {} } }
    return $false
}

function Check-WSL {
    try {
        $null = & wsl --version 2>$null
        if ($LASTEXITCODE -eq 0) { return $true }
    } catch { }
    try {
        $null = & wsl --list --quiet 2>$null
        if ($LASTEXITCODE -eq 0) { return $true }
    } catch { }
    return $false
}

function Check-WindowsRedis {
    try { if (Get-Command redis-server -ErrorAction SilentlyContinue) { return $true } } catch { }
    foreach ($p in @("C:\Program Files\Redis\redis-server.exe", "C:\redis\redis-server.exe", "$env:ProgramFiles\Redis\redis-server.exe")) {
        if (Test-Path $p) { return $true }
    }
    return $false
}

function Install-RedisWindows {
    Write-Host ""
    Write-Host "Redis is required but not installed." -ForegroundColor Yellow
    Write-Host "  1. Chocolatey: choco install redis-64 -y" -ForegroundColor Gray
    Write-Host "  2. Manual: https://github.com/tporadowski/redis/releases" -ForegroundColor Gray
    Write-Host ""
    Write-Host "For safety, this script will not auto-install Redis." -ForegroundColor Yellow
}

function Start-Redis {
    # Check if REDIS_URL points to a remote (non-localhost) Redis (e.g. Azure Redis)
    $envFile = Join-Path $BackendDir ".env"
    if (Test-Path $envFile) {
        $redisUrl = Get-Content $envFile | Where-Object { $_ -match '^\s*REDIS_URL\s*=' -and $_ -notmatch '^\s*#' } | Select-Object -Last 1
        if ($redisUrl -and $redisUrl -match 'rediss?://.*\.(redis\.cache\.windows\.net|redis\.cloud|upstash\.io|railway\.app)') {
            Write-Host "[OK] Using remote Redis (Azure/cloud) from REDIS_URL" -ForegroundColor Green
            return
        }
    }

    Write-Host "Checking Redis (Windows)..." -ForegroundColor Cyan
    if (Check-Redis) { Write-Host "[OK] Redis is already running" -ForegroundColor Green; return }
    $win = Check-WindowsRedis
    $wsl = Check-WSL
    # Prefer Windows Redis so backend/Celery (running on Windows) can connect to localhost:6379
    if ($win) {
        try {
            $exe = (Get-Command redis-server -ErrorAction SilentlyContinue).Source
            if (-not $exe) { foreach ($p in @("C:\Program Files\Redis\redis-server.exe", "C:\redis\redis-server.exe", "$env:ProgramFiles\Redis\redis-server.exe")) { if (Test-Path $p) { $exe = $p; break } } }
            if ($exe) {
                # Try Windows service first, then foreground process
                $null = Start-Process -FilePath $exe -ArgumentList "--service-run" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
                if (-not $?) { $null = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue }
                Start-Sleep -Seconds 3
                if (Check-Redis) { Write-Host "[OK] Redis started (Windows)" -ForegroundColor Green; return }
            }
        } catch { }
    }
    # Fallback: try WSL only if Windows Redis not installed or failed to start
    if ($wsl) {
        try {
            $null = Start-Process wsl -ArgumentList "bash", "-c", "redis-server --daemonize yes" -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue
            foreach ($i in 1..6) {
                Start-Sleep -Seconds 2
                if (Check-Redis) { Write-Host "[OK] Redis started (WSL)" -ForegroundColor Green; return }
            }
        } catch { }
    }
    if (-not $win -and -not $wsl) { Install-RedisWindows; Write-Host "[ERROR] Redis required. Install and retry." -ForegroundColor Red; exit 1 }
    # Not reachable from Windows - require Windows Redis for backend/Celery on Windows
    Write-Host "[WARNING] Redis must run on Windows for this setup. Start manually:" -ForegroundColor Yellow
    Write-Host "  1. Install: choco install redis-64 -y" -ForegroundColor Gray
    Write-Host "  2. Start:   redis-server   (or start the Redis service in services.msc)" -ForegroundColor Gray
    Write-Host "  3. Check:   redis-cli ping   (should return PONG)" -ForegroundColor Gray
    Write-Host "  See START-SERVICES.md section 'Redis failed to start' for details." -ForegroundColor Gray
}

