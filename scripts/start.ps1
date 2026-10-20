# Start CatCrewAI in your browser (no desktop shell needed).
#
#   .\scripts\start.ps1            # builds the web UI the first time, then opens http://127.0.0.1:8765
#   .\scripts\start.ps1 -Rebuild   # rebuild the web UI first (after pulling new code)
#
# The desktop app still works too: cd frontend; npm run tauri dev
param([switch]$Rebuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating the Python environment (.venv) ..." -ForegroundColor Cyan
    python -m venv .venv
    $python = ".venv\Scripts\python.exe"
    & $python -m pip install -r requirements.txt
}
if ($Rebuild -or -not (Test-Path "frontend\dist\index.html")) {
    Write-Host "Building the web UI ..." -ForegroundColor Cyan
    Push-Location frontend
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    Pop-Location
}
Write-Host "Starting CatCrewAI (Ctrl+C or Power off in the sidebar stops it) ..." -ForegroundColor Cyan
& $python -m backend --ui
