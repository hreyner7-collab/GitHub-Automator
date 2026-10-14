$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 5: WSL OFF + SERVICES + STARTUP + LEFTOVERS ====='

Log '--- WSL shutdown + unregister any distros ---'
wsl.exe --shutdown | Out-Null
Start-Sleep -Seconds 3
$distros = @(wsl.exe --list --quiet 2>&1 | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
if ($distros.Count -eq 0) { Log '    (no distros registered)' }
foreach ($d in $distros) { Log "    wsl --unregister $d"; wsl.exe --unregister $d 2>&1 | Out-Null }

Log '--- remove WSL appx packages ---'
Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'WindowsSubsystemForLinux|Wsl' } | ForEach-Object {
  Log ("    remove-appx: " + $_.Name)
  Remove-AppxPackage -Package $_.PackageFullName -AllUsers -ErrorAction SilentlyContinue
}

Log '--- disable services ---'
foreach ($svc in @('WslService','vmcompute','hns','com.docker.service')) {
  try { Set-Service -Name $svc -StartupType Disabled -ErrorAction Stop; Log "    disabled: $svc" }
  catch { Log "    (skip: $svc)" }
  Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
}

Log '--- clean startup entries (keep Epic/LGHUB/Chrome/Edge) ---'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
foreach ($n in @('OneDrive','GameCrackLauncher','Steam')) {
  Remove-ItemProperty -Path $runKey -Name $n -ErrorAction SilentlyContinue
  Log "    removed startup: $n"
}

Log '--- retry: Steam folder + empty app shells + Python leftover ---'
Get-Process steam,steamwebhelper,GameOverlayUI,SteamService -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
foreach ($d in @('C:\Program Files (x86)\Steam',
  "$env:LOCALAPPDATA\Programs\@omnirushdesktop",
  "$env:LOCALAPPDATA\Programs\@opencode-aidesktop",
  "$env:LOCALAPPDATA\Programs\Python")) {
  if (Test-Path $d) { Log "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
}
Remove-Item 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 284160' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop' -Recurse -Force -ErrorAction SilentlyContinue

Log '--- leftover folders ---'
$leftovers = @(
  "$env:USERPROFILE\OpenBot",
  "$env:USERPROFILE\OneDrive",
  "$env:USERPROFILE\.docker",
  "$env:APPDATA\Docker",
  "$env:LOCALAPPDATA\Docker",
  "$env:LOCALAPPDATA\Docker Desktop",
  "$env:APPDATA\Steam",
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Steam",
  'D:\SteamLibrary',
  'D:\GameCrack',
  'D:\OpenClawTray',
  'D:\docker',
  'D:\DockerDesktopWSL',
  'D:\wsl'
)
foreach ($d in $leftovers) {
  if (Test-Path $d) { Log "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
}

Log '--- leftover installer downloads ---'
foreach ($f in @("$env:USERPROFILE\Downloads\OpenBot-setup.exe", "$env:USERPROFILE\Downloads\Freebuff-0.0.154-win-x64.exe")) {
  if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue; Log "    rm $f" }
}

Log '===== CHUNK 5 DONE ====='
