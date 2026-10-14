$ErrorActionPreference = 'Continue'
$mine = $PID
$others = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.ProcessId -ne $mine -and $_.CommandLine -match 'cleanup-main' })
if ($others.Count -gt 0) { Write-Host "Another cleanup-main instance is running (PID $($others[0].ProcessId)) - exiting."; exit 0 }
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
Start-Transcript -Path $log -Append | Out-Null
function Log($m) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

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
  Log ">>> Uninstalling: $name"
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
      if ($cmd -notmatch 'Docker Desktop Installer') { $cmd = "$cmd /S" }
      $type = 'nsis-guess'
    }
  }
  Log "    cmd($type): $cmd"
  $p = Start-Process cmd.exe -ArgumentList '/c', $cmd -PassThru -ErrorAction SilentlyContinue
  if ($p) { $p.WaitForExit() } else { Log '    (Start-Process failed)' }
  $deadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    if (@(Get-TargetEntries -Patterns $name).Count -eq 0) { Log '    OK: removed'; return $true }
  }
  Log '    ?? still listed after wait'
  return $false
}

Log '===== UNINSTALL PASS ====='
$targets = @(
  'Docker Desktop','Kimi','OmniRush*','ZCode*','Kiro (User)','Qoder (User)','OpenCode*','OpenHands*','OpenClaw*',
  'Node.js*','Python 3.12*','Python Launcher','uv*','Eclipse Temurin*','FFmpeg*','Bun',
  'Opera*','Quick Share*','Steam','SteamUnlocked*','BeamNG*','Retrac*','SE7EN*'
)
$results = @{}
foreach ($pat in $targets) {
  foreach ($e in @(Get-TargetEntries -Patterns @($pat))) { $results[$e.DisplayName] = Invoke-UninstallEntry -e $e }
}
Log '===== UNINSTALL RESULTS ====='
$results.GetEnumerator() | ForEach-Object { $v = 'CHECK-MANUALLY'; if ($_.Value) { $v = 'REMOVED' }; Log ("{0} : {1}" -f $_.Key, $v) }

Log '===== ONEDRIVE ====='
$ods = @("$env:windir\System32\OneDriveSetup.exe", "$env:LOCALAPPDATA\Microsoft\OneDrive\OneDriveSetup.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ods) { Log "    via $ods /uninstall"; Start-Process $ods -ArgumentList '/uninstall' -Wait }
else { Log '    no OneDriveSetup found' }

Log '===== WSL / VIRTUALIZATION OFF ====='
wsl.exe --shutdown 2>$null
wsl.exe --list --quiet 2>$null | ForEach-Object { $n = ($_ -replace "`0", '').Trim(); if ($n) { Log "    wsl --unregister $n"; wsl.exe --unregister $n 2>$null | Out-Null } }
Get-AppxPackage -AllUsers *WindowsSubsystemForLinux* -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
Get-AppxPackage -AllUsers *Wsl* -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
foreach ($f in 'Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform','HypervisorPlatform','Microsoft-Hyper-V-All') {
  Log "    dism /Disable-Feature: $f"
  dism.exe /Online /Disable-Feature /FeatureName:$f /NoRestart | Out-Null
}
Set-Service WslService -StartupType Disabled -ErrorAction SilentlyContinue
Log '    WslService set to Disabled'

Log '===== LEFTOVER FOLDERS ====='
foreach ($d in @('D:\GameCrack','D:\OpenClawTray',"$env:USERPROFILE\OpenBot","$env:APPDATA\Docker","$env:LOCALAPPDATA\Docker","$env:USERPROFILE\.docker",'D:\docker','D:\DockerDesktopWSL','D:\wsl')) {
  if (Test-Path $d) { Log "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue } else { Log "    (absent) $d" }
}

Log '===== STARTUP ENTRIES ====='
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
foreach ($n in @('Opera Stable','Opera Browser Assistant','Steam','OneDrive','GameCrackLauncher','OpenClawTray','MicrosoftEdgeAutoLaunch_8BEF3EDBB44C2FCC8EE84BAAF23F5A0B')) {
  Remove-ItemProperty -Path $runKey -Name $n -ErrorAction SilentlyContinue
  Log "    removed startup: $n"
}

Log '===== LEFTOVER INSTALLERS ====='
foreach ($f in @("$env:USERPROFILE\Downloads\OpenBot-setup.exe","$env:USERPROFILE\Downloads\Freebuff-0.0.154-win-x64.exe")) {
  if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue; Log "    rm $f" }
}

Log '===== ALL DONE - REBOOT REQUIRED ====='
Stop-Transcript | Out-Null
