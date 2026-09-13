# One collection pass: BITS first, then Bowlit frame data.
#
# The order is not a preference. fetch_social.py only looks at matches BITS has
# already marked played, so running it first collects nothing -- it would just
# re-ask for whatever the previous BITS run happened to know about.
#
# Safe to run as often as you like: both collectors upsert, and each skips
# matches whose results it already holds.
[CmdletBinding()]
param(
    [int[]] $Seasons = @(2025, 2026),
    # Re-ask for matches previously found empty, past the usual retry window.
    [switch] $Refetch
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
$log = "$here\logs\collect.log"

function Write-Log($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

Write-Log "--- collect start (seasons: $($Seasons -join ', ')) ---"

$failed = 0
foreach ($step in @(
    @{ name = 'fetch_bits';   args = @("collector\fetch_bits.py")   + $Seasons },
    @{ name = 'fetch_social'; args = @("collector\fetch_social.py") + ($Seasons | ForEach-Object { '--season'; $_ }) },
    # Frames decoded off scoring.se boards, for the halls Bowlit never reaches.
    # After fetch_bits, because attributing a card to a player needs the scores
    # and handicaps BITS publishes; before notify, so a match is announced with
    # its shot statistics already on the page.
    @{ name = 'ingest_scoring'; args = @("collector\ingest_scoring.py", "--auto", "--write") },
    # Last, so a match is only announced once its results -- and its frame data,
    # from whichever source covers the hall -- are actually in the database.
    @{ name = 'notify_new';   args = @("collector\notify_new.py") }
)) {
    if ($Refetch -and $step.name -eq 'fetch_social') { $step.args += '--refetch' }
    Write-Log "$($step.name) ..."
    $out = & $python $step.args 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $out) { Write-Log "   $line" }
    if ($code -ne 0) {
        # Keep going: a BITS failure should not stop the frame-data pass from
        # picking up matches an earlier run already recorded as played.
        Write-Log "$($step.name) MISSLYCKADES (exit $code)"
        $failed++
    }
}

# Looking a week ahead once a day is plenty; the 08:00 run does it.
if ((Get-Date).Hour -lt 10) {
    Write-Log "plan_captures ..."
    & "$here\plan_captures.ps1" -Days 8 | Out-Null
}

Write-Log "--- collect klar, $failed steg misslyckades ---"
exit $failed
