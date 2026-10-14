$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) | Tee-Object -FilePath $log -Append | Write-Host }

Log '===== ELEV CHUNK 6: DISABLE VIRTUALIZATION FEATURES ====='
foreach ($f in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform','HypervisorPlatform','Microsoft-Hyper-V-All')) {
  Log "    dism /Disable-Feature: $f"
  $out = & dism.exe /Online /Disable-Feature /FeatureName:$f /NoRestart 2>&1
  $out | ForEach-Object { Log ("      " + $_.ToString().Trim()) }
}

Log '--- feature state after ---'
dism.exe /Online /Get-Features /Format:Table | Select-String -Pattern 'Linux|VirtualMachine|Hypervisor|Hyper-V' | ForEach-Object { Log ("    " + $_.Line.Trim()) }

Log '===== CHUNK 6 DONE ====='
