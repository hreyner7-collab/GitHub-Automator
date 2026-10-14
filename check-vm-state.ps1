Write-Host '=== VM/WSL processes now ==='
Get-Process vmmemWSL,vmwp,vmcompute,msrdc,wslhost,wslrelay,wslservice,'Docker Desktop','com.docker.backend',openbot-desktop -ErrorAction SilentlyContinue |
  Select-Object Name,Id,@{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB)}} | Format-Table -AutoSize

Write-Host '=== Services ==='
Get-Service WslService,vmcompute,hns,com.docker.service -ErrorAction SilentlyContinue |
  Select-Object Name,Status,StartType | Format-Table -AutoSize

Write-Host '=== Docker startup entry still gone? ==='
$v = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue)
if ($v.PSObject.Properties.Name -contains 'Docker Desktop') { Write-Host 'STILL PRESENT' } else { Write-Host 'GONE (good)' }

Write-Host '=== Running distros ==='
(wsl.exe --list --running 2>&1 | Out-String).Replace([char]0,'')
