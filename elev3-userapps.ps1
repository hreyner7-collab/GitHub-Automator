$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 3: OMNIRUSH + OPENCODE + STALE HKCU ENTRIES ====='

Log '--- OmniRush.ai ---'
$om = "$env:LOCALAPPDATA\Programs\@omnirushdesktop\Uninstall OmniRush.ai.exe"
if (Test-Path $om) {
  $p = Start-Process -FilePath $om -ArgumentList '/currentuser','/S' -PassThru
  if (-not $p.WaitForExit(60000)) { Log '    omnirush uninstaller hung, killing'; $p.Kill() } else { Log ("    omnirush exit: " + $p.ExitCode) }
} else { Log '    (uninstaller already gone)' }
if (Test-Path "$env:LOCALAPPDATA\Programs\@omnirushdesktop") { Log '    rm @omnirushdesktop'; Remove-Item "$env:LOCALAPPDATA\Programs\@omnirushdesktop" -Recurse -Force -ErrorAction SilentlyContinue }

Log '--- OpenCode ---'
$oc = "$env:LOCALAPPDATA\Programs\@opencode-aidesktop\Uninstall OpenCode.exe"
if (Test-Path $oc) {
  $p = Start-Process -FilePath $oc -ArgumentList '/currentuser','/S' -PassThru
  if (-not $p.WaitForExit(60000)) { Log '    opencode uninstaller hung, killing'; $p.Kill() } else { Log ("    opencode exit: " + $p.ExitCode) }
} else { Log '    (uninstaller already gone)' }
if (Test-Path "$env:LOCALAPPDATA\Programs\@opencode-aidesktop") { Log '    rm @opencode-aidesktop'; Remove-Item "$env:LOCALAPPDATA\Programs\@opencode-aidesktop" -Recurse -Force -ErrorAction SilentlyContinue }

Log '--- stale HKCU uninstall entries (folders already deleted) ---'
$stale = @(
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\01d4acb0-867c-5bbe-9589-216e6e9d5452',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\268ce9e6-a30b-5890-ad18-d4b3ebba5377',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{A2CA08B5-C756-463E-B13D-F051F4F11F0B}_is1',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{943D6004-554E-4B49-A1D5-52F999A1B3C9}_is1'
)
foreach ($k in $stale) {
  if (Test-Path $k) { Log "    rm $k"; Remove-Item $k -Recurse -Force -ErrorAction SilentlyContinue }
}

Log '--- user-scope app data leftovers ---'
foreach ($d in @("$env:APPDATA\OmniRush", "$env:APPDATA\OpenCode", "$env:APPDATA\Kimi", "$env:APPDATA\ZCode", "$env:APPDATA\Kiro", "$env:APPDATA\Qoder", "$env:LOCALAPPDATA\omnirush", "$env:LOCALAPPDATA\opencode")) {
  if (Test-Path $d) { Log "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
}

Log '--- verify ---'
foreach ($d in @("$env:LOCALAPPDATA\Programs\@omnirushdesktop", "$env:LOCALAPPDATA\Programs\@opencode-aidesktop")) {
  Log ("    exists {0} : {1}" -f $d, (Test-Path $d))
}
Log '===== CHUNK 3 DONE ====='
