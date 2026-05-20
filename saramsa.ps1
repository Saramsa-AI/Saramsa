# Saramsa - master CLI
# Usage: saramsa start | saramsa kill | saramsa help
# Delegates to saramsa-scripts/start-procfile.ps1, kill.ps1, help.ps1

# Read args via $args (not param()) so dash-prefixed tokens like '-f' don't
# get intercepted by PowerShell's parameter binder before reaching subcommands.
$Command = if ($args.Count -gt 0) { [string]$args[0] } else { "" }
$Arg1    = if ($args.Count -gt 1) { [string]$args[1] } else { "" }
$Arg2    = if ($args.Count -gt 2) { [string]$args[2] } else { "" }

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsDir = Join-Path $ScriptDir "saramsa-scripts"

if (-not (Test-Path $ScriptsDir)) {
    Write-Host "[ERROR] saramsa-scripts not found: $ScriptsDir" -ForegroundColor Red
    exit 1
}

$startProcfileScript = Join-Path $ScriptsDir "start-procfile.ps1"
$killScript = Join-Path $ScriptsDir "kill.ps1"
$logScript = Join-Path $ScriptsDir "log.ps1"
$helpScript = Join-Path $ScriptsDir "help.ps1"

switch ($Command.ToLower()) {
    "start" {
        if ($Arg1 -or $Arg2) {
            Write-Host "[ERROR] 'saramsa start' does not take an environment. Use 'saramsa start'." -ForegroundColor Red
            exit 1
        }
        & $startProcfileScript
    }
    "help" {
        & $helpScript
    }
    "kill" {
        if ($Arg1 -or $Arg2) {
            Write-Host "[ERROR] 'saramsa kill' does not take any arguments. Use 'saramsa kill'." -ForegroundColor Red
            exit 1
        }
        & $killScript
    }
    "log" {
        if (-not $Arg1) {
            Write-Host "[ERROR] Missing log target. Use 'saramsa log frontend' or 'saramsa log all'." -ForegroundColor Red
            exit 1
        }
        & $logScript $Arg1 $Arg2
    }
    default {
        if (-not $Command) {
            Write-Host "[ERROR] No command. Use 'saramsa help'." -ForegroundColor Red
            Write-Host ""
            Write-Host "  saramsa start" -ForegroundColor Yellow
            Write-Host "  saramsa kill" -ForegroundColor Yellow
            Write-Host "  saramsa log frontend" -ForegroundColor Yellow
        } else {
            Write-Host "[ERROR] Unknown command: $Command. Use 'saramsa help'." -ForegroundColor Red
        }
        exit 1
    }
}
