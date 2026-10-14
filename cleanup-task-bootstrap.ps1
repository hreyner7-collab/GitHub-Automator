$main = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-main.ps1"
$taskName = 'OneTimeCleanupTask'

# remove stale copy if a previous attempt left one
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $main + '"')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Host "Scheduled task '$taskName' registered; starts in 5 seconds as SYSTEM."

# also fire it immediately, belt and suspenders
Start-ScheduledTask -TaskName $taskName
Write-Host 'Task started.'
