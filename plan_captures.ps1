# Register a capture task for every upcoming fixture at a scoring.se hall.
#
# Run it weekly (or let collect.ps1 call it). It is safe to re-run: a task that
# already exists is left alone, and tasks for matches that have been played are
# removed. Nothing here decides anything -- plan_captures.py works out which
# fixtures need capturing, from the fixture list and the lane group BITS gives.
[CmdletBinding()]
param(
    [int]    $Days     = 8,
    [int]    $Interval = 30,
    [switch] $WhatIfOnly
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
$log = "$here\logs\collect.log"
function Write-Log($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

$jobs = & $python "collector\plan_captures.py" --days $Days | Where-Object { $_ } |
        ForEach-Object { $_ | ConvertFrom-Json }

Write-Log "plan_captures: $($jobs.Count) matcher att fanga inom $Days dagar"

foreach ($j in $jobs) {
    if (Get-ScheduledTask -TaskName $j.name -ErrorAction SilentlyContinue) {
        Write-Log "   $($j.name): finns redan"
        continue
    }
    if ($WhatIfOnly) {
        Write-Log "   $($j.name): skulle skapas  $($j.start) banor $($j.lanes) till $($j.until)"
        continue
    }
    # Two kinds of capture. A scoring.se hall is photographed lane by lane;
    # a hall with its own feed is recorded by capture_live.ps1, at whatever
    # interval board.LIVE_HALLS says for it -- Helsingborg needs 5s, because
    # its board hides the second player the instant a serie ends.
    if ($j.kind -eq 'live') {
        $la = "-Source $($j.args.source) -Lanes $($j.lanes) " +
              "-Until $($j.until) -Interval $($j.interval)"
        if ($j.args.center) { $la += " -Center $($j.args.center)" }
        if ($j.args.hall)   { $la += " -Hall $($j.args.hall)" }
        if ($j.args.uuid)   { $la += " -Uuid $($j.args.uuid)" }
        $script = "capture_live.ps1"
    } else {
        $la = "-Alley $($j.alley) -Lanes $($j.lanes) -Until $($j.until) " +
              "-Interval $Interval"
        $script = "capture_scoring.ps1"
    }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
      -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
                 "`"$here\$script`" $la") `
      -WorkingDirectory $here
    $trigger  = New-ScheduledTaskTrigger -Once -At $j.start
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
      -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
      -ExecutionTimeLimit (New-TimeSpan -Hours 5) -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $j.name -Action $action -Trigger $trigger `
          -Settings $settings -User $env:USERNAME -RunLevel Limited `
          -Description "$($j.teams) i $($j.hall), banor $($j.lanes)" | Out-Null
        Write-Log "   $($j.name): skapad  $($j.start) banor $($j.lanes)"
    } catch {
        Write-Log "   $($j.name): KUNDE INTE SKAPAS -- $($_.Exception.Message)"
    }
}

# Tidy up: a capture task whose match has been played has done its job.
$played = & $python -c @"
import sys; sys.path.insert(0, 'collector')
from store import connect
for r in connect().execute('SELECT match_id FROM bits_match WHERE has_been_played=1'):
    print(r[0])
"@ | ForEach-Object { $_.Trim() }

Get-ScheduledTask | Where-Object { $_.TaskName -like 'LumaScoring*' -or
                                   $_.TaskName -like 'LumaLive*' } | ForEach-Object {
    $id = ($_.TaskName -split '_')[-1]
    $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
    if ($played -contains $id -and $_.State -ne 'Running' -and $info.LastRunTime.Year -gt 1999) {
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
        Write-Log "   $($_.TaskName): borttagen, matchen ar spelad och fangad"
    }
}
