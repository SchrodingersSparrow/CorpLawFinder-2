# Build the Windows installer for Legal Knowledge Manager (Stage 8).
#
# Run from the project root in PowerShell:
#
#     .\build-installer.ps1
#
# Needs (one-time, on the build machine only): Python 3.12+ and Node.js LTS.
# The PEOPLE WHO RUN THE INSTALLER need neither — everything is bundled.
#
# If PowerShell refuses to run scripts:  right-click > Run with PowerShell,
# or first run:  Set-ExecutionPolicy -Scope Process Bypass

$ErrorActionPreference = "Stop"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Green }

Step "Checking tools"
python --version; if ($LASTEXITCODE -ne 0) { throw "Python not found - install from python.org (tick 'Add python.exe to PATH')." }
node --version;   if ($LASTEXITCODE -ne 0) { throw "Node.js not found - install the LTS from nodejs.org." }

Step "Installing backend packages (incl. PyInstaller)"
python -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed." }

Step "Running the backend test suite first (no point packaging a broken build)"
python -m unittest discover -s backend/tests
if ($LASTEXITCODE -ne 0) { throw "Backend tests failed - fix before packaging." }

Step "Freezing the backend into backend-dist\lkm-backend"
python backend/scripts/freeze_backend.py
if ($LASTEXITCODE -ne 0) { throw "Backend freeze failed." }

Step "Building the desktop app + installer"
Push-Location frontend
try {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
    npm test
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed - fix before packaging." }
    npm run dist
    if ($LASTEXITCODE -ne 0) { throw "electron-builder failed." }
} finally {
    Pop-Location
}

$installer = Get-ChildItem "frontend/dist-installer" -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Step "Done"
Write-Host "Installer: $($installer.FullName)" -ForegroundColor Yellow
Write-Host "Double-click it on any Windows 10/11 machine - no Python, no Node needed there."
