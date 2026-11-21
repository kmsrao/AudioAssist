<#
.SYNOPSIS
  Start Live Audio Assist (Windows).
.DESCRIPTION
  Starts the FastAPI backend, which also serves the compiled dashboard, and
  opens it in the default browser. Logs stream to this terminal and to
  logs\app.log. Stop with Ctrl+C.
.EXAMPLE
  .\start.ps1
  .\start.ps1 -Port 9000 -NoBrowser
  .\start.ps1 -Dev                # backend + Angular dev server (hot reload) on :4200
#>
param(
    [int]$Port = 8765,
    [string]$BindHost = '127.0.0.1',
    [switch]$NoBrowser,
    [switch]$Dev,
    [string]$LogLevel = 'INFO'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$VenvPy = Join-Path $Backend '.venv\Scripts\python.exe'
$Dist = Join-Path $Frontend 'dist\live-audio-assist\browser\index.html'

if (-not (Test-Path $VenvPy)) {
    Write-Host '[error] Python environment not found. Run .\setup.ps1 first.' -ForegroundColor Red
    exit 1
}
if (-not $Dev -and -not (Test-Path $Dist)) {
    Write-Host '[warn] Compiled dashboard not found - run .\setup.ps1 (the API still works; a placeholder page is shown).' -ForegroundColor Yellow
}

$pyArgs = @('-m', 'app.main', '--host', $BindHost, '--port', $Port, '--log-level', $LogLevel)
if ($NoBrowser -or $Dev) { $pyArgs += '--no-browser' }

Push-Location $Backend
try {
    if ($Dev) {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) { Write-Host '[error] npm not found (needed for -Dev).' -ForegroundColor Red; exit 1 }
        Write-Host "Starting backend on :$Port (background) and Angular dev server on :4200 ..." -ForegroundColor Cyan
        $backendProc = Start-Process -FilePath $VenvPy -ArgumentList $pyArgs -NoNewWindow -PassThru
        try {
            Push-Location $Frontend
            if (-not $NoBrowser) { Start-Job -ScriptBlock { Start-Sleep 8; Start-Process 'http://localhost:4200' } | Out-Null }
            & $npm.Source start
        } finally {
            Pop-Location
            if ($backendProc -and -not $backendProc.HasExited) { Stop-Process -Id $backendProc.Id -Force }
        }
    } else {
        & $VenvPy @pyArgs
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
            Write-Host "[error] Backend exited with code $LASTEXITCODE (see messages above and logs\app.log)." -ForegroundColor Red
            exit $LASTEXITCODE
        }
    }
} finally {
    Pop-Location
}
