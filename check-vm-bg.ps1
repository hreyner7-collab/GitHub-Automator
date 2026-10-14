Write-Host '=== VM/WSL/Docker processes ==='
Get-Process -Name vmmemWSL,vmwp,vmcompute,'Docker Desktop','Docker Desktop Backend','com.docker.backend' -ErrorAction SilentlyContinue |
  Select-Object Name,Id,@{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB)}} | Format-Table -AutoSize

Write-Host '=== Related services ==='
Get-Service vmcompute,hns,WslService,LxssManager,com.docker.service -ErrorAction SilentlyContinue |
  Select-Object Name,DisplayName,Status,StartType | Format-Table -AutoSize

Write-Host '=== Startup entries (Docker) ==='
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue |
  Select-Object * -ExcludeProperty PS* | Format-List

Write-Host '=== Docker Desktop auto-start setting ==='
$ss = "$env:APPDATA\Docker\settings-store.json"
if (Test-Path $ss) { (Get-Content $ss -Raw | ConvertFrom-Json) | Select-Object AutoStart,OpenUIOnStartupDisabled,DisplayedOnboarding | Format-List } else { Write-Host "settings-store.json not found at $ss" }
