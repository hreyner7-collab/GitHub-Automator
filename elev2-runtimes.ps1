$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 2: PYTHON 3.12 + RETRAC + SE7EN + OPENHANDS-REG ====='

Log '--- Python 3.12.10 bundle (removes all components + launcher) ---'
$py = "$env:LOCALAPPDATA\Package Cache\{b6ce88eb-2ce3-4d91-8efc-425ae1f48caf}\python-3.12.10-amd64.exe"
if (Test-Path $py) {
  $p = Start-Process -FilePath $py -ArgumentList '/uninstall','/quiet' -PassThru
  if (-not $p.WaitForExit(100000)) { Log '    python uninstaller hung, killing'; $p.Kill() } else { Log ("    python bundle exit: " + $p.ExitCode) }
} else { Log '    (python bundle installer not in package cache)' }

Log '--- Python Launcher (msi) ---'
Start-Process msiexec.exe -ArgumentList '/X{665A0435-D5D5-4A49-9DE0-FBC23C5425ED}','/qn','/norestart' -Wait
Log '    done'

Log '--- Retrac (msi) ---'
Start-Process msiexec.exe -ArgumentList '/X{B64341E3-8AF2-4B11-8395-8748AE7D1423}','/qn','/norestart' -Wait
Log '    done'

Log '--- SE7EN Service (inno) ---'
$se7 = 'C:\Program Files\7Launcher\Service\Uninstall\unins000.exe'
if (Test-Path $se7) {
  $p = Start-Process -FilePath $se7 -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -PassThru
  if (-not $p.WaitForExit(60000)) { Log '    se7en uninstaller hung, killing'; $p.Kill() } else { Log ("    se7en exit: " + $p.ExitCode) }
} else { Log '    (se7en uninstaller already gone)' }
if (Test-Path 'C:\Program Files\7Launcher') { Log '    rm C:\Program Files\7Launcher'; Remove-Item 'C:\Program Files\7Launcher' -Recurse -Force -ErrorAction SilentlyContinue }

Log '--- OpenHands Agent Canvas registry entry (folder already gone) ---'
Remove-Item 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\27351982-5360-5242-b25a-50e086edf209' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\27351982-5360-5242-b25a-50e086edf209' -Recurse -Force -ErrorAction SilentlyContinue

Log '--- verify ---'
foreach ($d in @("$env:LOCALAPPDATA\Programs\Python", 'C:\Program Files\Retrac', 'C:\Program Files\7Launcher')) {
  Log ("    exists {0} : {1}" -f $d, (Test-Path $d))
}
Log '===== CHUNK 2 DONE ====='
