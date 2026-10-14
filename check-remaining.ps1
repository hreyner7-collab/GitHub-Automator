$paths = @(
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Write-Host '=== STILL INSTALLED (from full target list) ==='
$patterns = @('OpenBot*','Docker Desktop','Kimi','OmniRush*','ZCode*','Kiro*','Qoder*','OpenCode*','OpenHands*','OpenClaw*','Node.js*','Python*','uv*','Eclipse Temurin*','FFmpeg*','Bun','Opera*','Quick Share*','Steam','SteamUnlocked*','BeamNG*','Retrac*','SE7EN*')
Get-ItemProperty $paths -ErrorAction SilentlyContinue |
  Where-Object { $d = $_.DisplayName; $d -and ($patterns | Where-Object { $d -like $_ }) } |
  Sort-Object DisplayName -Unique |
  Select-Object DisplayName, PSPath | ForEach-Object { $k = if ($_.PSPath -match 'HKCU') {'user'} elseif ($_.PSPath -match 'WOW6432') {'hklm32'} else {'hklm'}; "$_".Replace('@{DisplayName=', '').Replace('}', '') + " [$k]" }

Write-Host ''
Write-Host '=== VM/WSL/Docker still running? ==='
Get-Process vmmemWSL,vmwp,vmcompute,wslservice,msrdc,wslhost,wslrelay -ErrorAction SilentlyContinue | Select-Object Name,Id | Format-Table -AutoSize
