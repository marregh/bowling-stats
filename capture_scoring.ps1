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
    [double] $Hours    = 3,          # a four-game match runs ~2h10m, not the
                                     # 1h40m the BITS schedule implies
    [int]    $Interval = 75
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# Text has to survive two hops: Python's stdout, and PowerShell decoding it.
# Left alone, Python emits cp1252 and PowerShell reads cp437, which turned
# "banor på skärmen" into "banor pσ skΣrmen" in the log. Pin both to UTF-8.
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

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
else               { $args += @("--hours", $Hours) }

# Not Tee-Object: it has no -Encoding in PowerShell 5.1 and writes UTF-16,
# which left a log that was half UTF-8 (written interactively) and half UTF-16
# (written by the scheduled task) and readable as neither. Add-Content takes an
# explicit encoding, and is what collect.ps1 already uses.
function Write-Log($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

Write-Log ("start: hall {0}, banor {1}{2}" -f $Alley, $Lanes,
           $(if ($Until) { ", till $Until" } else { "" }))

# $ErrorActionPreference is deliberately relaxed for this one call. In
# PowerShell 5.1 a native command's stderr, redirected with 2>&1, arrives as
# NativeCommandError records -- and under 'Stop' the *first* such line kills
# the script. On 2026-09-19 that ended the Baltiska capture at 14:44 without
# writing either the error or the "slut" line, and the match's last two series
# were lost. The point of a capture is to keep recording; a line on stderr must
# be logged, not fatal.
$ErrorActionPreference = 'Continue'
& $python $args 2>&1 | ForEach-Object { Write-Log "   $_" }
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

Write-Log "slut (exit $code)"
