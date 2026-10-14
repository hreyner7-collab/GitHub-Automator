$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 4: STEAM + ONEDRIVE + UV/FFMPEG WINGET PKGS ====='

Log '--- Steam ---'
Get-Process steam,steamwebhelper,GameOverlayUI,SteamService -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Stop-Service SteamService -Force -ErrorAction SilentlyContinue
$su = 'C:\Program Files (x86)\Steam\uninstall.exe'
if (Test-Path $su) {
  $p = Start-Process -FilePath $su -ArgumentList '/S' -PassThru
  if (-not $p.WaitForExit(90000)) { Log '    steam uninstaller hung, killing'; $p.Kill() } else { Log ("    steam uninstall exit: " + $p.ExitCode) }
} else { Log '    (steam uninstaller already gone)' }
if (Test-Path 'C:\Program Files (x86)\Steam') {
  Log '    removing Steam folder'
  Remove-Item 'C:\Program Files (x86)\Steam' -Recurse -Force -ErrorAction SilentlyContinue
}
Remove-Item 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 284160' -Recurse -Force -ErrorAction SilentlyContinue
Log ("    Steam folder exists: " + (Test-Path 'C:\Program Files (x86)\Steam'))

Log '--- BeamNG leftover library folder ---'
if (Test-Path 'D:\SteamLibrary') { Log '    rm D:\SteamLibrary'; Remove-Item 'D:\SteamLibrary' -Recurse -Force -ErrorAction SilentlyContinue }

Log '--- OneDrive ---'
Get-Process OneDrive -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
foreach ($od in @("$env:windir\SysWOW64\OneDriveSetup.exe", "$env:windir\System32\OneDriveSetup.exe", "$env:LOCALAPPDATA\Microsoft\OneDrive\OneDriveSetup.exe")) {
  if (Test-Path $od) {
    Log "    $od /uninstall"
    $p = Start-Process -FilePath $od -ArgumentList '/uninstall' -PassThru
    if (-not $p.WaitForExit(90000)) { Log '    onedrive setup hung, killing'; $p.Kill() } else { Log ("    onedrive exit: " + $p.ExitCode) }
    break
  }
}
if (Test-Path "$env:LOCALAPPDATA\Microsoft\OneDrive") { Log '    rm OneDrive folder'; Remove-Item "$env:LOCALAPPDATA\Microsoft\OneDrive" -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path "$env:USERPROFILE\OneDrive") { Log '    rm ~/OneDrive'; Remove-Item "$env:USERPROFILE\OneDrive" -Recurse -Force -ErrorAction SilentlyContinue }

Log '--- uv + FFmpeg winget packages ---'
foreach ($pkg in @('astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe', 'Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe')) {
  $pdir = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\$pkg"
  if (Test-Path $pdir) { Log "    rm $pdir"; Remove-Item $pdir -Recurse -Force -ErrorAction SilentlyContinue }
  $reg = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$pkg"
  if (Test-Path $reg) { Log "    rm reg $pkg"; Remove-Item $reg -Recurse -Force -ErrorAction SilentlyContinue }
}

Log '===== CHUNK 4 DONE ====='
