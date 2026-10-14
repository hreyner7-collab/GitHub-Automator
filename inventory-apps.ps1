$ErrorActionPreference = 'SilentlyContinue'

Write-Host '=== INSTALLED APPS (machine + user) ==='
$paths = @(
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty $paths |
  Where-Object { $_.DisplayName -and -not $_.SystemComponent } |
  Sort-Object DisplayName -Unique |
  Select-Object DisplayName, DisplayVersion, Publisher | Format-Table -AutoSize -Wrap

Write-Host '=== RUNNING NON-WINDOWS PROCESSES (grouped, by RAM) ==='
Get-Process | Where-Object { $_.Path -and $_.Path -notmatch '^C:\\Windows' } |
  Group-Object Name | ForEach-Object {
    [PSCustomObject]@{
      Name   = $_.Name
      Count  = $_.Count
      RAM_MB = [math]::Round(($_.Group | Measure-Object WorkingSet64 -Sum).Sum/1MB)
      Path   = $_.Group[0].Path
    }
  } | Sort-Object RAM_MB -Descending | Format-Table -AutoSize -Wrap

Write-Host '=== STARTUP ENTRIES (user + machine) ==='
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue |
  Select-Object * -Exclude PS* | Format-List
