# Fortnite optimization tweaks (Lecctron 2026 guide) - elevated section
# Logs to scripts\fortnite_tweaks_elevated.log for verification afterwards.
Start-Transcript -Path "$PSScriptRoot\fortnite_tweaks_elevated.log" -Force | Out-Null
$ErrorActionPreference = 'Continue'

Write-Host '== 1. System restore point =='
Enable-ComputerRestore -Drive "$env:SystemDrive\" -ErrorAction SilentlyContinue
# Frequency=0 guarantees a point is created even if one was made recently
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore' `
    -Name SystemRestorePointCreationFrequency -Value 0 -PropertyType DWord -Force | Out-Null
Checkpoint-Computer -Description 'Before Fortnite optimization tweaks' -RestorePointType MODIFY_SETTINGS
if ($?) { Write-Host '   Restore point created.' } else { Write-Host '   WARNING: restore point not confirmed.' }

Write-Host '== 2. MMCSS: Games task -> Scheduling Category High =='
$games = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games'
if (-not (Test-Path $games)) { New-Item -Path $games -Force | Out-Null }
Set-ItemProperty -Path $games -Name 'Scheduling Category' -Value 'High'

Write-Host '== 3. NetworkThrottlingIndex = ffffffff, SystemResponsiveness = 10 =='
$sp = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'
# reg.exe handles DWORD 0xffffffff cleanly (-1 = ffffffff)
reg add "$sp" /v NetworkThrottlingIndex /t REG_DWORD /d -1 /f
Set-ItemProperty -Path $sp -Name SystemResponsiveness -Value 10 -Type DWord

Write-Host '== 4. Win32PrioritySeparation = 0x26 =='
reg add 'HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl' /v Win32PrioritySeparation /t REG_DWORD /d 0x26 /f

Write-Host '== 5. Hardware-Accelerated GPU Scheduling (HAGS) on =='
reg add 'HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' /v HwSchMode /t REG_DWORD /d 2 /f

Write-Host '== 6. Core Isolation: Memory Integrity OFF =='
$hvci = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'
New-Item -Path $hvci -Force | Out-Null
Set-ItemProperty -Path $hvci -Name Enabled -Value 0 -Type DWord
Write-Host '   Set Enabled=0 (effective after reboot; toggle visible in Windows Security).'

Write-Host '== 7. Unlock Ultimate Performance power plan =='
$out = (powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61) | Out-String
Write-Host $out
if ($out -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') {
    $plan = $Matches[1]
    powercfg -setactive $plan
    Write-Host "   Activated plan $plan"
} else {
    Write-Host '   Could not parse new plan GUID - Ultimate Performance created but not activated.'
}

Write-Host '== 8. Launching Chris Titus Winutil (GUI opens in its own window) =='
Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','irm https://christitus.com/win | iex'

Write-Host 'ALL-ELEVATED-TWEAKS-DONE'
Stop-Transcript | Out-Null
