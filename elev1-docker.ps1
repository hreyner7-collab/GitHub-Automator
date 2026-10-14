$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 1: DOCKER DESKTOP + FEATURE STATE ====='

Log '--- feature state before ---'
dism.exe /Online /Get-Features /Format:Table | Select-String -Pattern 'Linux|VirtualMachine|Hypervisor|Hyper-V' | ForEach-Object { Log ("    " + $_.Line.Trim()) }

Log '--- stop docker ---'
Get-Process 'Docker Desktop','com.docker.build','dockerd','vpnkit' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Stop-Service com.docker.service -Force -ErrorAction SilentlyContinue

Log '--- run Docker Desktop uninstaller ---'
$inst = 'C:\Program Files\Docker\Docker\Docker Desktop Installer.exe'
if (Test-Path $inst) {
  $p = Start-Process -FilePath $inst -ArgumentList 'uninstall' -PassThru
  if (-not $p.WaitForExit(100000)) { Log '    installer hung, killing'; $p.Kill() } else { Log ("    installer exit: " + $p.ExitCode) }
} else { Log '    (installer already gone)' }

Log '--- unregister WSL distros (docker-desktop data) ---'
wsl.exe --shutdown | Out-Null
Start-Sleep -Seconds 3
$distros = @(wsl.exe --list --quiet 2>&1 | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
if ($distros.Count -eq 0) { Log '    (no distros registered)' }
foreach ($d in $distros) {
  Log "    wsl --unregister $d"
  wsl.exe --unregister $d 2>&1 | Out-Null
}

Log '--- remove leftover Docker folders ---'
foreach ($d in @('C:\Program Files\Docker', "$env:APPDATA\Docker", "$env:LOCALAPPDATA\Docker", "$env:USERPROFILE\.docker", 'D:\docker', 'D:\DockerDesktopWSL', 'D:\wsl')) {
  if (Test-Path $d) { Log "    rm $d"; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
}

Log ("    Docker folder exists: " + (Test-Path 'C:\Program Files\Docker'))
Log '===== CHUNK 1 DONE ====='
