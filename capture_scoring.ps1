# Capture a match at a scoring.se hall (Baltiska by default). Ctrl+C to stop.
#
# Unlike the BITS collectors, this one is only useful while the match is being
# played -- scoring.se serves no past days at all. Which lanes to watch is in
# BITS: ListMatches gives matchAlleyGroupName, e.g. "5 - 12".
param(
    [int]    $Alley    = 524,        # Baltiska Bowlinghallen (Malmö)
    [string] $Lanes    = "5-12",
    [string] $Until    = "",         # HH:MM, local
    [int]    $Minutes  = 0,
    [int]    $Interval = 75
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$python = if (Test-Path "$here\.venv\Scripts\python.exe") {
    "$here\.venv\Scripts\python.exe"
} else {
    (Get-Command python).Source
}

New-Item -ItemType Directory -Force -Path "$here\logs" | Out-Null
$log = "$here\logs\scoring.log"

$args = @("collector\capture_scoring.py", "--alley", $Alley, "--lanes", $Lanes,
          "--interval", $Interval)
if ($Until)        { $args += @("--until", $Until) }
elseif ($Minutes)  { $args += @("--minutes", $Minutes) }

"{0}  start: hall {1}, banor {2}{3}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),
    $Alley, $Lanes, $(if ($Until) { ", till $Until" } else { "" }) |
    Tee-Object -FilePath $log -Append

& $python $args 2>&1 | ForEach-Object {
    "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_ |
        Tee-Object -FilePath $log -Append
}

"{0}  slut (exit {1})" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE |
    Tee-Object -FilePath $log -Append
