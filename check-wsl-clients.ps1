Write-Host '=== Parents of WSL client processes ==='
$(foreach ($p in Get-CimInstance Win32_Process -Filter "Name='msrdc.exe' OR Name='wslhost.exe' OR Name='wslrelay.exe' OR Name='wslservice.exe'") {
  $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
  [PSCustomObject]@{
    Child  = "$($p.Name) [$($p.ProcessId)]"
    Parent = if ($parent) { "$($parent.Name) [$($parent.ProcessId)]" } else { "gone (PID $($p.ParentProcessId))" }
    ParentPath = $parent.Path
  }
}) | Format-List

Write-Host '=== Docker remnants ==='
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'docker' -or $_.Name -match 'docker' } |
  Select-Object Name,ProcessId,CommandLine | Format-List
