$paths = @(
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$pats = @('Docker Desktop','Kimi','OmniRush*','ZCode*','Kiro*','Qoder*','OpenCode*','OpenHands*','Python 3.12*','Python Launcher','BeamNG*','Steam','Retrac','SE7EN*','FFmpeg*','uv')
Get-ItemProperty $paths -ErrorAction SilentlyContinue |
  Where-Object { $d = $_.DisplayName; $d -and ($pats | Where-Object { $d -like $_ }) } |
  Sort-Object DisplayName -Unique |
  ForEach-Object {
    Write-Host ("### {0}" -f $_.DisplayName)
    Write-Host ("    scope-key : {0}" -f ($_.PSPath -replace 'Microsoft.PowerShell.Core\\Registry::',''))
    Write-Host ("    quiet     : {0}" -f $_.QuietUninstallString)
    Write-Host ("    uninstall : {0}" -f $_.UninstallString)
    Write-Host ("    publisher : {0}" -f $_.Publisher)
    Write-Host ""
  }
