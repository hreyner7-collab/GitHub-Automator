$ErrorActionPreference = 'Continue'
Start-Transcript -Path "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt" -Append | Out-Null

Write-Host '===== PHASE 1: STOP EVERYTHING ====='
# Shut down WSL/Docker first
wsl.exe --shutdown 2>$null
foreach ($s in 'WslService','vmcompute','hns','com.docker.service') { Stop-Service $s -Force -ErrorAction SilentlyContinue }

# Kill app processes being removed (NOT: Freebuff, Epic, Chrome, Spokenly, LGHUB, Edge webview, python? pythonw killed)
$killNames = @('steam','steamwebhelper','opera','OneDrive','FileCoAuth','nearby_share','GameCrackLauncher','OpenClaw.Tray.WinUI','Kimi','ZCode','Qoder','Kiro','KiroProxy','OpenCode','OpenHands','OmniRush','openbot-desktop','Docker Desktop','Docker Desktop Backend','com.docker.backend','com.docker.build','com.docker.dev-envs','pythonw','msrdc','wslhost','wslrelay')
foreach ($n in $killNames) { Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue }
# bun: only standalone (Freebuff's bundled bun must survive)
Get-Process bun -ErrorAction SilentlyContinue | Where-Object { $_.Path -notlike '*@codebufffreebuff-desktop*' } | Stop-Process -Force
# crashpad_handler only under NearbyShare
Get-Process crashpad_handler -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*NearbyShare*' } | Stop-Process -Force
# stop possible related services
Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'SE7EN|Retrac|OmniRush|ZCode|Kiro' } | Stop-Service -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Write-Host 'Processes stopped.'

Write-Host '===== PHASE 2: UNINSTALL - OpenBot stack + AI assistants ====='
$targets = @('OpenBot*','Docker Desktop','Kimi','OmniRush.ai*','ZCode*','Kiro (User)','Qoder (User)','OpenCode*','OpenHands*','OpenClaw*')

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
    $type = 'normal+flags'
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
  Write-Host "    cmd($type): $cmd"
  if ($name -eq 'Microsoft OneDrive') {
    $ods = @("$env:windir\System32\OneDriveSetup.exe", "$env:LOCALAPPDATA\Microsoft\OneDrive\OneDriveSetup.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($ods) { Write-Host "    OneDrive special: $ods /uninstall"; Start-Process $ods -ArgumentList '/uninstall' -Wait }
    else { Start-Process cmd.exe -ArgumentList '/c', $cmd -Wait }
  } else {
    Start-Process cmd.exe -ArgumentList '/c', $cmd -Wait -ErrorAction SilentlyContinue
  }
  # wait up to 120s for the entry to disappear
  $deadline = (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    $still = @(Get-TargetEntries -Patterns $name).Count
    if ($still -eq 0) { Write-Host "    OK: removed"; return $true }
  }
  Write-Host "    ?? still listed after wait (may have failed silently)"
  return $false
}

$results = @{}
foreach ($pat in $targets) {
  $entries = @(Get-TargetEntries -Patterns @($pat))
  if ($entries.Count -eq 0) { Write-Host ">>> No registry entry for pattern: $pat"; continue }
  foreach ($e in $entries) { $results[$e.DisplayName] = Invoke-UninstallEntry -e $e }
}

# Qualcomm 'Installer' special case
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -and $_.Publisher -like '*Qualcomm*' } | ForEach-Object {
    $results[$_.DisplayName] = Invoke-UninstallEntry -e $_
  }

Write-Host '===== PHASE 2 RESULTS ====='
$results.GetEnumerator() | ForEach-Object { $v = 'CHECK-MANUALLY'; if ($_.Value) { $v = 'REMOVED' }; Write-Host ("{0} : {1}" -f $_.Key, $v) }

Stop-Transcript | Out-Null
