$ErrorActionPreference = 'Continue'
Start-Transcript -Path "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt" -Append | Out-Null

function Get-TargetEntries {
  param([string[]]$Patterns)
  $paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  Get-ItemProperty $paths -ErrorAction SilentlyContinue |
    Where-Object { $d = $_.DisplayName; $d -and ($Patterns | Where-Object { $d -like $_ }) } |
    Sort-Object DisplayName -Unique
}

function Invoke-UninstallEntry {
  param($e)
  $name = $e.DisplayName
  Write-Host ">>> Uninstalling: $name"
  $cmd = $e.QuietUninstallString
  $type = 'quiet'
  if (-not $cmd) {
    $cmd = $e.UninstallString
    $type = 'raw'
    if ($cmd -match 'msiexec') {
      $cmd = $cmd -ireplace '/I','/X'
      if ($cmd -notmatch '/qn') { $cmd = "$cmd /qn /norestart" }
      $type = 'msi'
    } elseif ($cmd -match 'unins000\.exe') {
      $cmd = "$cmd /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
      $type = 'inno'
    } elseif ($cmd -match '(?i)uninstall') {
      $cmd = "$cmd /S"
      $type = 'nsis-guess'
    }
  }
  Write-Host "    cmd($type): $cmd"
  Start-Process cmd.exe -ArgumentList '/c', $cmd -Wait -ErrorAction SilentlyContinue
  $deadline = (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    $still = @(Get-TargetEntries -Patterns $name).Count
    if ($still -eq 0) { Write-Host "    OK: removed"; return $true }
  }
  Write-Host "    ?? still listed after wait"
  return $false
}

Write-Host '===== PHASE 3: RUNTIMES / EVERYDAY / GAMES / UNKNOWN ====='
$targets = @('Node.js*','Python 3.12*','Python Launcher','uv*','Eclipse Temurin*','FFmpeg*','Bun','Opera*','Quick Share*','Steam','SteamUnlocked*','BeamNG*','Retrac*','SE7EN*')
$results = @{}
foreach ($pat in $targets) {
  $entries = @(Get-TargetEntries -Patterns @($pat))
  if ($entries.Count -eq 0) { Write-Host ">>> No registry entry for pattern: $pat"; continue }
  foreach ($e in $entries) { $results[$e.DisplayName] = Invoke-UninstallEntry -e $e }
}
Write-Host '===== PHASE 3 RESULTS ====='
$results.GetEnumerator() | ForEach-Object { $v = 'CHECK-MANUALLY'; if ($_.Value) { $v = 'REMOVED' }; Write-Host ("{0} : {1}" -f $_.Key, $v) }

Write-Host '===== PHASE 4: VIRTUALIZATION OFF + ONE DRIVE + LEFTOVERS + STARTUP ====='

# OneDrive (per-user uninstaller, machine entries may not exist)
$ods = @("$env:windir\System32\OneDriveSetup.exe", "$env:LOCALAPPDATA\Microsoft\OneDrive\OneDriveSetup.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ods) {
  Write-Host ">>> OneDrive via $ods /uninstall"
  Start-Process $ods -ArgumentList '/uninstall' -Wait
} else {
  Get-TargetEntries -Patterns @('OneDrive') | ForEach-Object { Invoke-UninstallEntry -e $_ }
}

# Unregister WSL distros (deletes docker-desktop data vhds)
Write-Host '>>> Unregistering WSL distros'
wsl.exe --list --quiet 2>$null | ForEach-Object { $n = ($_ -replace "`0", '').Trim(); if ($n) { Write-Host "    wsl --unregister $n"; wsl.exe --unregister $n 2>$null | Out-Null } }

# Uninstall WSL appx + disable virtualization features
Write-Host '>>> Removing WSL (appx) and disabling virtualization optional features'
Get-AppxPackage -AllUsers *WindowsSubsystemForLinux* -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
Get-AppxPackage -AllUsers *Wsl* -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
dism.exe /Online /Disable-Feature /FeatureName:Microsoft-Windows-Subsystem-Linux /NoRestart
dism.exe /Online /Disable-Feature /FeatureName:VirtualMachinePlatform /NoRestart
dism.exe /Online /Disable-Feature /FeatureName:HypervisorPlatform /NoRestart
dism.exe /Online /Disable-Feature /FeatureName:Microsoft-Hyper-V-All /NoRestart
# Guard against re-enabling: set WSL service disabled
Set-Service WslService -StartupType Disabled -ErrorAction SilentlyContinue

Write-Host '>>> Deleting leftover folders'
$leftovers = @(
  'D:\GameCrack',
  'D:\OpenClawTray',
  "$env:USERPROFILE\OpenBot",
  "$env:APPDATA\Docker",
  "$env:LOCALAPPDATA\Docker",
  "$env:USERPROFILE\.docker"
)
foreach ($d in $leftovers) {
  if (Test-Path $d) { Write-Host "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } else { Write-Host "    (not present) $d" }
}
# docker-desktop-data may live on D:
foreach ($vhd in @('D:\docker','D:\DockerDesktopWSL','D:\wsl')) {
  if (Test-Path $vhd) { Write-Host "    rm $vhd"; Remove-Item $vhd -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host '>>> Cleaning startup entries (keep Epic/Chrome/LGHUB/Freebuff)'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$remove = @('Opera Stable','Opera Browser Assistant','Steam','OneDrive','GameCrackLauncher','OpenClawTray','MicrosoftEdgeAutoLaunch_8BEF3EDBB44C2FCC8EE84BAAF23F5A0B')
foreach ($n in $remove) { Remove-ItemProperty -Path $runKey -Name $n -ErrorAction SilentlyContinue; Write-Host "    removed startup: $n" }

# Downloads installers cleanup
Write-Host '>>> Removing leftover installers'
foreach ($f in @("$env:USERPROFILE\Downloads\OpenBot-setup.exe", "$env:USERPROFILE\Downloads\Freebuff-0.0.154-win-x64.exe")) {
  if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue; Write-Host "    rm $f" }
}

Stop-Transcript | Out-Null
Write-Host 'DONE - REBOOT REQUIRED to finish disabling virtualization'
