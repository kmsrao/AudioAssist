<#
.SYNOPSIS
  One-time setup for Live Audio Assist on Windows.
.DESCRIPTION
  1. Finds Python 3.12+ (prefers 3.13 / 3.12)
  2. Creates backend\.venv
  3. Installs Python dependencies
  4. Installs frontend dependencies (npm)
  5. Builds the Angular dashboard
  6. Validates audio (PortAudio) and configuration
.EXAMPLE
  .\setup.ps1
  .\setup.ps1 -SkipFrontend   # backend only (e.g. no Node.js available)
#>
param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Venv = Join-Path $Backend '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [warn] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "    [error] $msg" -ForegroundColor Red; exit 1 }

Write-Host 'Live Audio Assist - setup (Windows)' -ForegroundColor White

# ------------------------------------------------------------------ 1. Python
Step 'Detecting Python 3.12+'
# Space-separated candidates (avoids PowerShell 5.1 single-element array unrolling).
$candidates = @('py -3.13', 'py -3.12', 'py -3.14', 'python', 'python3')
$python = $null
foreach ($cand in $candidates) {
    $parts = $cand.Split(' ')
    $exe = $parts[0]
    $pyArgs = @()
    if ($parts.Length -gt 1) { $pyArgs = $parts[1..($parts.Length - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        # no double quotes in -c code: Windows PowerShell 5.1 strips them when calling native programs
        $ver = & $exe @pyArgs -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
    } catch { continue }
    if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
    $nums = "$ver".Trim().Split(' ')
    if ($nums.Length -ge 2 -and [int]$nums[0] -eq 3 -and [int]$nums[1] -ge 12) {
        $python = @{ exe = $exe; args = $pyArgs; version = "$($nums[0]).$($nums[1])" }
        break
    }
}
if (-not $python) {
    Fail 'Python 3.12 or newer not found. Install it from https://www.python.org/downloads/ (or: winget install Python.Python.3.13) and re-run.'
}
Ok "Python $($python.version) ($($python.exe) $($python.args -join ' '))"
if ([int]$python.version.Split('.')[1] -ge 14) {
    Warn 'Python 3.14+ detected. If NumPy/SciPy wheels are missing for it, install Python 3.13 and re-run.'
}

# ------------------------------------------------------------------ 2. venv
Step 'Creating virtual environment (backend\.venv)'
if (Test-Path $VenvPy) {
    Ok 'already exists'
} else {
    & $python.exe @($python.args) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed' }
    Ok 'created'
}

# ------------------------------------------------------------------ 3. Python deps
Step 'Installing Python dependencies'
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Backend 'requirements-dev.txt') --quiet
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed (see output above)' }
Ok 'installed'

# ------------------------------------------------------------------ 4/5. Frontend
if ($SkipFrontend) {
    Warn 'Skipping frontend build (-SkipFrontend). The backend will show a placeholder page.'
} else {
    Step 'Checking Node.js / npm'
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm -and (Test-Path 'C:\Program Files\nodejs\npm.cmd')) {
        $env:Path = "C:\Program Files\nodejs;$env:Path"
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        Fail 'Node.js (npm) not found. Install Node.js LTS (https://nodejs.org or: winget install OpenJS.NodeJS.LTS), open a new terminal and re-run. Or use -SkipFrontend.'
    }
    $nodeVer = & node --version
    Ok "Node $nodeVer"

    Step 'Installing frontend dependencies (npm ci)'
    Push-Location $Frontend
    try {
        if (Test-Path 'package-lock.json') { & $npm.Source ci --no-audit --no-fund } else { & $npm.Source install --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) { Fail 'npm install failed' }
        Ok 'installed'

        Step 'Building the dashboard (Angular production build)'
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { Fail 'Angular build failed' }
        Ok 'built -> frontend\dist\live-audio-assist\browser'
    } finally {
        Pop-Location
    }
}

# ------------------------------------------------------------------ 6. Validation
Step 'Validating audio subsystem and configuration'
Push-Location $Backend
try {
    # piped via stdin: avoids native-argument quoting problems in Windows PowerShell 5.1
    @'
from app.config.settings import ServerConfig, AnalysisSettings
from app.database.db import Database
cfg = ServerConfig.from_env()
cfg.data_dir.mkdir(parents=True, exist_ok=True)
cfg.logs_dir.mkdir(parents=True, exist_ok=True)
Database(cfg.db_path)
AnalysisSettings()
print("    [ok] configuration and database (" + str(cfg.db_path) + ")")
try:
    import sounddevice as sd
    devs = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
    print("    [ok] PortAudio " + sd.get_portaudio_version()[1] + " - " + str(len(devs)) + " input device(s) visible")
except Exception as exc:
    print("    [warn] PortAudio not usable (" + str(exc) + "). Audio capture will not be available.")
'@ | & $VenvPy -
    if ($LASTEXITCODE -ne 0) { Fail 'validation failed' }
} finally {
    Pop-Location
}

Write-Host "`nSetup complete. Start the application with:" -ForegroundColor Green
Write-Host '    .\start.ps1'
