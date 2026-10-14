$main = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-main.ps1"
Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File', $main) -WindowStyle Hidden
Write-Host "Detached cleanup launched from $main"
