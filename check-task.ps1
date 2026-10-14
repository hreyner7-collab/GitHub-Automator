$taskName = 'OneTimeCleanupTask'
$t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($t) {
  Write-Host ("State: {0}" -f $t.State)
  $i = $t | Get-ScheduledTaskInfo
  Write-Host ("LastRunTime: {0}  LastTaskResult: 0x{1:X} ({1})  NumberOfMissedRuns: {2}" -f $i.LastRunTime, $i.LastTaskResult, $i.NumberOfMissedRuns)
  Write-Host ("NextRunTime: {0}" -f $i.NextRunTime)
} else { Write-Host 'TASK NOT FOUND' }

Write-Host ''
Write-Host '=== Any powershell spawned by task? ==='
Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='wscript.exe' OR Name='cmd.exe'" |
  Select-Object ProcessId, CommandLine | Format-List

Write-Host ''
Write-Host '=== Task Scheduler log (last operational events) ==='
Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -MaxEvents 15 -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'OneTimeCleanupTask' } |
  ForEach-Object { Write-Host ("[{0}] {1}: {2}" -f $_.TimeCreated, $_.Id, ($_.Message -replace "`r`n",' ')) }
