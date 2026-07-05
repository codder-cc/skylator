# Install the Skylator remote worker as an auto-restarting Windows service via NSSM.
#
# Prereq: nssm.exe on PATH (https://nssm.cc). Run this in an elevated PowerShell.
#
# A Windows service with AppExit=Restart means the worker comes back after a crash or
# reboot, which (with the durable ResultStore) lets a months-long run survive unattended.

param(
    [Parameter(Mandatory=$true)][string]$HostUrl,         # e.g. http://192.168.1.100:5000
    [string]$InstallDir = "C:\skylator\remote_worker",
    [string]$ModelPath  = ""                              # optional --model-path
)

$python = Join-Path $InstallDir "venv\Scripts\python.exe"
$args   = "server.py --host-url $HostUrl"
if ($ModelPath -ne "") { $args += " --model-path `"$ModelPath`"" }

nssm install SkylatorAgent $python $args
nssm set SkylatorAgent AppDirectory $InstallDir
nssm set SkylatorAgent AppExit Default Restart        # auto-restart on exit/crash
nssm set SkylatorAgent AppRestartDelay 5000           # 5s
nssm set SkylatorAgent Start SERVICE_AUTO_START       # start on boot
nssm start SkylatorAgent

Write-Host "SkylatorAgent installed and started. Manage with: nssm {start|stop|restart|remove} SkylatorAgent"
