$main = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-main.ps1"
# Spawn via WMI so the process is parented to WmiPrvSE (survives this shell being torn down)
$cmd = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $main + '"'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
Write-Host ("WMI Create returned: ReturnValue={0} ProcessId={1}" -f $r.ReturnValue, $r.ProcessId)
