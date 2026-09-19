# Run the capture test harness and leave a report behind.
#
# Scheduled to fire after a test capture finishes, so the result is on disk
# whether or not anyone is watching at the time. Writes logs/test_capture.log
# and leaves the exit code as the harness returned it.
[CmdletBinding()]
param(
    [int]    $Alley = 524,
    [string] $Day   = (Get-Date -Format 'yyyy-MM-dd'),
    [string] $From  = '00:00',
    [string] $To    = '23:59',
    [string] $Lanes = '5-12',
    [int[]]  $Match = @()
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = if (Test-Path "$here\.venv\Scripts\python.exe") {
    "$here\.venv\Scripts\python.exe"
} else { (Get-Command python).Source }

New-Item -ItemType Directory -Force -Path "$here\logs" | Out-Null
$log = "$here\logs\test_capture.log"

$a = @("tools\test_capture.py", "--alley", $Alley, "--day", $Day,
       "--from", $From, "--to", $To, "--lanes", $Lanes)
foreach ($m in $Match) { $a += @("--match", $m) }

Add-Content -Path $log -Value ("=" * 66) -Encoding utf8
Add-Content -Path $log -Value ("kord {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding utf8

# 'Continue', for the same reason capture_scoring.ps1 uses it: under 'Stop' the
# first line the harness writes to stderr would end the script before the
# report reached the log.
$ErrorActionPreference = 'Continue'
& $python $a 2>&1 | ForEach-Object {
    Write-Host $_
    Add-Content -Path $log -Value "$_" -Encoding utf8
}
$code = $LASTEXITCODE
Add-Content -Path $log -Value "slut (exit $code)" -Encoding utf8
exit $code
