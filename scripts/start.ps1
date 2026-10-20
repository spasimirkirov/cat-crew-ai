# Start CatCrewAI in your browser, with a tray icon and no console window.
#
#   .\scripts\start.ps1             # builds the web UI the first time, then starts in the tray and opens the browser
#   .\scripts\start.ps1 -Rebuild    # rebuild the web UI first (after pulling new code)
#   .\scripts\start.ps1 -Shortcut   # also put a "CatCrewAI" shortcut on the desktop
#   .\scripts\start.ps1 -Console    # run in this console instead (to see the logs live)
#
# Closing the browser tab leaves CatCrewAI running: the tray icon opens it again (double-click or
# "Open CatCrewAI") and "Stop CatCrewAI" shuts it down. Starting it again while it runs just opens
# the browser. Logs: data\logs\backend.log. The desktop app still works too: cd frontend; npm run tauri dev
param([switch]$Rebuild, [switch]$Shortcut, [switch]$Console)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating the Python environment (.venv) ..." -ForegroundColor Cyan
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt
} else {
    # find_spec only looks the packages up (no import, nothing on stderr).
    & ".venv\Scripts\python.exe" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('pystray') and u.find_spec('PIL') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing the tray icon packages ..." -ForegroundColor Cyan
        & ".venv\Scripts\python.exe" -m pip install pystray pillow
    }
}
if ($Rebuild -or -not (Test-Path "frontend\dist\index.html")) {
    Write-Host "Building the web UI ..." -ForegroundColor Cyan
    Push-Location frontend
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    Pop-Location
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if ($Shortcut) {
    $link = Join-Path ([Environment]::GetFolderPath("Desktop")) "CatCrewAI.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($link)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "-m backend --tray"
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root "frontend\src-tauri\icons\icon.ico")
    $lnk.Description = "CatCrewAI"
    $lnk.Save()
    Write-Host "Shortcut created: $link" -ForegroundColor Green
}

if ($Console) {
    & ".venv\Scripts\python.exe" -m backend --ui
} else {
    Start-Process -FilePath $pythonw -ArgumentList "-m", "backend", "--tray" -WorkingDirectory $root -WindowStyle Hidden
    Write-Host "CatCrewAI is starting in the tray (look for the cat icon near the clock)." -ForegroundColor Green
}
