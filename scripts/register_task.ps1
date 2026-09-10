<#
    Registers (or updates) a Windows Scheduled Task that runs the monitor once a
    day. Run this script from an elevated PowerShell, or it will fall back to a
    per-user task that only fires while you are logged in.

        powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 07:15

    Remove it with:  Unregister-ScheduledTask -TaskName "RightmoveEdinburghMonitor"
#>
param(
    [string]$Time = "07:15",
    [string]$TaskName = "RightmoveEdinburghMonitor"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $projectRoot "scripts\run_monitor.cmd"

if (-not (Test-Path $runner)) {
    throw "Cannot find $runner"
}

$action   = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $projectRoot
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
# Spread the scrape over a 20-minute window so it isn't a fixed hit each day.
$trigger.RandomDelay = "PT20M"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Daily Rightmove + UK HPI snapshot for Edinburgh" `
    -Force

Write-Host "Registered '$TaskName' to run daily at $Time."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName '$TaskName'"
