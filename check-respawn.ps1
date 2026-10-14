Write-Host '=== Anything Docker/WSL/OpenBot still running ==='
Get-Process | Where-Object {
  ($_.Path -and $_.Path -match 'Docker|\\wsl|OpenBot|\.bun') -or
  ($_.Name -match 'docker|wsl|vmmem|vmwp|vmcompute')
} | Select-Object Name,Id,Path | Format-Table -AutoSize -Wrap

Write-Host '=== What is using the WSL VM (parent of vmwp) ==='
Get-CimInstance Win32_Process -Filter "Name='vmwp.exe' OR Name='vmmemWSL' OR Name='vmcompute.exe'" |
  Select-Object Name,ProcessId,ParentProcessId,CommandLine | Format-List

Write-Host '=== Scheduled tasks mentioning docker/wsl ==='
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'docker|wsl' -or $_.TaskPath -match 'docker|wsl' } |
  Select-Object TaskPath,TaskName,State | Format-Table -AutoSize
