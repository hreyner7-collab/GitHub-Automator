$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 7: KILL ORPHANED DOCKER INSTALLER ====='
$p = Get-Process -Id 13392 -ErrorAction SilentlyContinue
if ($p) {
  Log ("    killing Docker Desktop Installer PID 13392 (elevated)")
  taskkill.exe /F /PID 13392 /T 2>&1 | ForEach-Object { Log ("      " + $_.ToString().Trim()) }
  Start-Sleep -Seconds 3
} else { Log '    (already gone)' }
if (Get-Process -Id 13392 -ErrorAction SilentlyContinue) { Log '    STILL ALIVE' } else { Log '    confirmed gone' }
Log '===== CHUNK 7 DONE ====='
