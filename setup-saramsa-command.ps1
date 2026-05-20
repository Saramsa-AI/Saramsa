# Setup script to add 'saramsa' command to PowerShell
# Run this once: .\setup-saramsa-command.ps1

$ScriptPath = $PSScriptRoot
$ScriptFile = Join-Path $ScriptPath "saramsa.ps1"

if (-not (Test-Path $ScriptFile)) {
    Write-Host "[ERROR] saramsa.ps1 not found at: $ScriptFile" -ForegroundColor Red
    exit 1
}

$ProfilePath = $PROFILE.CurrentUserAllHosts
$ProfileDir = Split-Path -Parent $ProfilePath

if (-not (Test-Path $ProfileDir)) {
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
}

$FunctionCode = @"

# Saramsa Service Manager Function
# No param() block: lets dash-prefixed args (e.g. '-f' for 'saramsa log backend -f')
# pass through to saramsa.ps1 instead of being intercepted by PowerShell's
# parameter binder. @args splats all positional args to the script.
function saramsa {
    `$ScriptPath = '$ScriptPath'
    `$ScriptFile = Join-Path `$ScriptPath "saramsa.ps1"

    if (Test-Path `$ScriptFile) {
        & `$ScriptFile @args
    } else {
        Write-Host "[ERROR] saramsa.ps1 not found at: `$ScriptFile" -ForegroundColor Red
    }
}

"@

$ProfileContent = ""
if (Test-Path $ProfilePath) {
    $ProfileContent = Get-Content $ProfilePath -Raw
}

if ($ProfileContent -match "function saramsa") {
    Write-Host "[WARNING] 'saramsa' function already exists in profile." -ForegroundColor Yellow
    Write-Host "   Updating to latest version..." -ForegroundColor Yellow
    # (?ms): single-line so '.' crosses newlines, multi-line so '^}' anchors
    # the closing brace at the start of a line. Without (?m), the old regex
    # only matched a '}' at start-of-string and silently failed, causing
    # repeated reinstalls to stack duplicate functions in the profile.
    $NewContent = $ProfileContent -replace "(?ms)# Saramsa Service Manager Function.*?^\}", ""
    $NewContent = $NewContent.TrimEnd() + "`n$FunctionCode"
    Set-Content -Path $ProfilePath -Value $NewContent -Encoding UTF8
    Write-Host "[OK] Function updated! Restart PowerShell or run: . `$PROFILE.CurrentUserAllHosts" -ForegroundColor Green
} else {
    Add-Content -Path $ProfilePath -Value "`n$FunctionCode"
    Write-Host "[OK] 'saramsa' function added to PowerShell profile!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Profile location: $ProfilePath" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Please restart PowerShell or run:" -ForegroundColor Yellow
    Write-Host "   . `$PROFILE.CurrentUserAllHosts" -ForegroundColor White
    Write-Host ""
    Write-Host "Then you can use:" -ForegroundColor Yellow
    Write-Host "   saramsa start" -ForegroundColor White
}
