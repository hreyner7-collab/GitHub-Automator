Write-Host '=== cleanup-main process running? ==='
$procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'cleanup-main' }
if ($procs) { $procs | ForEach-Object { Write-Host ("RUNNING PID {0}: {1}" -f $_.ProcessId, $_.CommandLine) } } else { Write-Host 'NOT RUNNING' }

Write-Host ''
Write-Host '=== Last 25 lines of log ==='
Get-Content "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt" -Tail 25

Write-Host ''
Write-Host '=== Log last modified ==='
(Get-Item "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt").LastWriteTime
Get-Date
