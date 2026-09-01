# Installs the dashboard as an auto-starting Windows service via NSSM.
# MUST be run as Administrator.
#
# The app binds 127.0.0.1 only. Put a reverse proxy (Caddy, nginx, IIS) in front
# of it to terminate TLS on your public host and proxy
# <your-domain>/<prefix> -> 127.0.0.1:<port>.
param(
  [string]$Nssm        = "nssm.exe",     # path to nssm.exe, or leave it on PATH
  [int]   $Port        = 8768,
  [string]$Prefix      = "luma-bowling", # no leading slash; see README
  [string]$ServiceName = "LumaBowling"
)
$ErrorActionPreference = 'Continue'
$base    = $PSScriptRoot
$python  = (Get-Command python).Source
$logdir  = Join-Path $base "logs"

New-Item -ItemType Directory -Force -Path $logdir | Out-Null

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host "ERROR: not elevated. Re-open PowerShell as Administrator." -ForegroundColor Red
  exit 1
}

# Accept either a full path or a bare name resolved off PATH.
if (-not (Test-Path $Nssm)) {
  $found = Get-Command $Nssm -ErrorAction SilentlyContinue
  if (-not $found) {
    Write-Host "ERROR: nssm not found ($Nssm). Install NSSM or pass -Nssm <path>." -ForegroundColor Red
    exit 1
  }
  $Nssm = $found.Source
}

# Refuse to install over whatever already owns the port. Windows lets two
# sockets bind the same port and silently serves whichever answers first, so a
# collision here would look healthy and serve the wrong app. See README.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -ne 0 }
if ($busy) {
  $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($busy[0].OwningProcess)" -ErrorAction SilentlyContinue
  Write-Host "ERROR: port $Port is already served by PID $($busy[0].OwningProcess) $($owner.CommandLine)" -ForegroundColor Red
  Write-Host "Free it or pass -Port <other>." -ForegroundColor Red
  exit 1
}

# stop and remove any prior instance so this script is re-runnable
& $Nssm stop    $ServiceName 2>$null | Out-Null
& $Nssm remove  $ServiceName confirm 2>$null | Out-Null

& $Nssm install $ServiceName $python "serve.py --port $Port"
& $Nssm set $ServiceName AppDirectory        (Join-Path $base "web")
& $Nssm set $ServiceName AppEnvironmentExtra "LUMA_URL_PREFIX=$Prefix"
& $Nssm set $ServiceName DisplayName         "LUMA Bowling dashboard"
& $Nssm set $ServiceName Description          "Flask/waitress stats site behind a reverse proxy"
& $Nssm set $ServiceName Start               SERVICE_AUTO_START
& $Nssm set $ServiceName AppStdout           (Join-Path $logdir "service.log")
& $Nssm set $ServiceName AppStderr           (Join-Path $logdir "service.err.log")
& $Nssm set $ServiceName AppRotateFiles      1
& $Nssm set $ServiceName AppRotateBytes      5242880
& $Nssm start $ServiceName

Start-Sleep -Seconds 3
Get-Service $ServiceName | Format-Table Name,Status,StartType -AutoSize
try {
  $r = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10
  Write-Host "local check: HTTP $($r.StatusCode)" -ForegroundColor Green
} catch { Write-Host "local check FAILED: $_" -ForegroundColor Red }
Write-Host "`nNow proxy https://<your-domain>/$Prefix -> 127.0.0.1:$Port" -ForegroundColor Cyan
