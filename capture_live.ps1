# Record a hall's own live feed for the length of a match.
#
# The counterpart to capture_scoring.ps1, for halls that publish a feed rather
# than board images on scoring.se. Same shape and same reasons: a log that
# survives the run, and stderr that is logged rather than fatal.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Source,   # qubica | falkenberg | lanetalk
    [string] $Center,
    [string] $Hall,
    [string] $Uuid,
    [string] $Lanes    = "1-20",
    [string] $Until    = "",
    [int]    $Interval = 30
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = if (Test-Path "$here\.venv\Scripts\python.exe") {
    "$here\.venv\Scripts\python.exe"
} else { (Get-Command python).Source }

New-Item -ItemType Directory -Force -Path "$here\logs" | Out-Null
$log = "$here\logs\live_$Source.log"

function Write-Log($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

$a = @("collector\capture_live.py", "--source", $Source, "--lanes", $Lanes,
       "--interval", $Interval)
if ($Center) { $a += @("--center", $Center) }
if ($Hall)   { $a += @("--hall",   $Hall) }
if ($Uuid)   { $a += @("--uuid",   $Uuid) }
if ($Until)  { $a += @("--until",  $Until) }

Write-Log ("start: {0}, banor {1}, var {2}s{3}" -f $Source, $Lanes, $Interval,
           $(if ($Until) { ", till $Until" } else { "" }))

# 'Continue' for the call, for the reason capture_scoring.ps1 documents: under
# 'Stop' the first line Python writes to stderr arrives as a NativeCommandError
# and kills the script before it can be logged, which is how a capture died
# silently 95 minutes early on 2026-09-19.
$ErrorActionPreference = 'Continue'
& $python $a 2>&1 | ForEach-Object { Write-Log "   $_" }
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

Write-Log "slut (exit $code)"
exit $code
