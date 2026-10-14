$ErrorActionPreference = 'Continue'

Write-Host '=== BEFORE: VM/WSL processes ==='
Get-Process vmmemWSL,vmwp,vmcompute,msrdc,wslhost,wslrelay,wslservice -ErrorAction SilentlyContinue |
  Select-Object Name,Id | Format-Table -AutoSize

Write-Host '=== Running WSL distros ==='
(wsl.exe --list --running 2>&1 | Out-String).Replace([char]0,'')

Write-Host '=== Shutting down WSL VM ==='
(wsl.exe --shutdown 2>&1 | Out-String).Replace([char]0,'')
Start-Sleep 3

Write-Host '=== Stopping WSL service (kills wslhost/msrdc/wslrelay) ==='
Stop-Service WslService -Force -ErrorAction Continue
Start-Sleep 2

Write-Host '=== Setting WSL service to Manual (no boot start, still demand-startable for Docker/OpenBot) ==='
Set-Service WslService -StartupType Manual -ErrorAction Continue

Write-Host '=== Stopping Hyper-V Host Compute Service (vmcompute) ==='
Stop-Service vmcompute -Force -ErrorAction Continue
Start-Sleep 5

Write-Host '=== AFTER: should be empty ==='
Get-Process vmmemWSL,vmwp,vmcompute,msrdc,wslhost,wslrelay,wslservice -ErrorAction SilentlyContinue |
  Select-Object Name,Id | Format-Table -AutoSize

Write-Host '=== Service states ==='
Get-Service WslService,vmcompute,hns,com.docker.service -ErrorAction SilentlyContinue |
  Select-Object Name,Status,StartType | Format-Table -AutoSize
