# Start the master if it is not already up.
#
# Windows Update rebooted this box twice in twenty-four hours. Each time the master did
# not come back, and each time the agents kept translating into their own durable stores
# while nothing was delivered and nothing was visible -- ten hours of it on the first
# occasion, eleven on the second. The work survives, which is the whole point of the
# offline design, but the operator sees a dead dashboard and the fleet cannot be fed.
#
# Safe to run on a timer: it does nothing when the port is already served, so a boot
# trigger and a five-minute watchdog can be the same task.

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root 'venv\Scripts\python.exe'
$Log    = Join-Path $Root 'logs\keep_master_running.log'

function Write-Line($msg) {
    $dir = Split-Path -Parent $Log
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Add-Content -LiteralPath $Log
}

# Listening is the test, not "is there a python.exe". The box runs several, and a master
# that is up but wedged is a different problem from one that is not running at all.
$listening = $null -ne (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue)
if ($listening) { exit 0 }

if (-not (Test-Path $Python)) {
    Write-Line "venv python not found at $Python -- cannot start"
    exit 1
}

Write-Line 'port 5000 is not served -- starting the master'
Start-Process -FilePath $Python `
              -ArgumentList 'web_server.py', '--host', '0.0.0.0', '--log-level', 'INFO' `
              -WorkingDirectory $Root `
              -WindowStyle Hidden

# Give it long enough to bind before reporting, so the log says what actually happened
# rather than just that a start was attempted.
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
        Write-Line 'master is up'
        exit 0
    }
}
Write-Line 'master did not bind port 5000 within 90s'
exit 1
