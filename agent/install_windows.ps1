# DentCenter Monitor - Windows installer
# Run from an ADMIN PowerShell:  powershell -ExecutionPolicy Bypass -File install_windows.ps1
#
# Installs the monitoring agent and registers it to start at every user logon.
# Employees should use standard (non-admin) accounts so they can't remove it.

$ErrorActionPreference = "Stop"

# --- Config (already filled in for this deployment) -------------------------
$ServerUrl = "https://monitor-server-production-aa8610de.up.railway.app"
$ApiKey    = "lC_p8xcXOVWa5O5OQJG0kt4c4IWrqF1ztjtnvXn1W6k"
$InstallDir = "C:\ProgramData\DentCenterMonitor"
# ---------------------------------------------------------------------------

Write-Host "Installing DentCenter Monitor agent..." -ForegroundColor Cyan

# 1. Locate Python
$py = (Get-Command py -ErrorAction SilentlyContinue)
if ($py) { $python = "py"; $pyArgs = "-3" }
else {
    $python = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $python) { throw "Python 3 not found. Install it from https://python.org (check 'Add to PATH')." }
    $python = "python"; $pyArgs = ""
}

# 2. Copy agent into place
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Force "$PSScriptRoot\monitor_agent.py" $InstallDir
Write-Host "Copied agent to $InstallDir"

# 3. Install Python dependencies
Write-Host "Installing Python packages..."
& $python $pyArgs -m pip install --upgrade pip | Out-Null
& $python $pyArgs -m pip install psutil mss Pillow pystray | Out-Null

# 4. Resolve pythonw.exe (runs with no console window)
$pyExe = & $python $pyArgs -c "import sys,os; print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"
if (-not (Test-Path $pyExe)) { $pyExe = & $python $pyArgs -c "import sys; print(sys.executable)" }

# 5. Store config as machine-wide environment variables
[Environment]::SetEnvironmentVariable("MON_SERVER_URL", $ServerUrl, "Machine")
[Environment]::SetEnvironmentVariable("MON_API_KEY",    $ApiKey,    "Machine")

# 6. Register a scheduled task that runs at logon for all users (in their session)
$action  = New-ScheduledTaskAction -Execute $pyExe -Argument "`"$InstallDir\monitor_agent.py`"" -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
# S-1-5-32-545 = the built-in "Users" group -> task runs for whoever logs in
$principal = New-ScheduledTaskPrincipal -GroupId "S-1-5-32-545" -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "DentCenterMonitor" -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "`nDone. The agent will start at the next logon." -ForegroundColor Green
Write-Host "To start it now without logging off, run:  schtasks /Run /TN DentCenterMonitor"
Write-Host "To remove it later:  schtasks /Delete /TN DentCenterMonitor /F"
