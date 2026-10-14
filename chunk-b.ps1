$log = "$env:USERPROFILE\Documents\free\app\Cruality Pipeline\cleanup-log.txt"
function Log($m) { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

function Get-TargetEntries {
  param([string[]]$Patterns)
  $paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  Get-ItemProperty $paths -ErrorAction SilentlyContinue |
    Where-Object { $d = $_.DisplayName; $d -and ($Patterns | Where-Object { $d -like $_ }) } |
    Sort-Object DisplayName -Unique
}

function Invoke-UninstallEntry {
  param($e, [int]$MaxWaitSec = 60)
  $name = $e.DisplayName
  Log ">>> START $name"
  $cmd = $e.QuietUninstallString
  if (-not $cmd) {
    $cmd = $e.UninstallString
    if ($cmd -match 'msiexec') {
      $cmd = $cmd -ireplace '/I','/X'
      if ($cmd -notmatch '/qn') { $cmd = "$cmd /qn /norestart" }
    } elseif ($cmd -match 'unins000\.exe') {
      $cmd = "$cmd /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
    } elseif ($cmd -match '(?i)uninstall') {
      $cmd = "$cmd /S"
    }
  }
  Log "    cmd: $cmd"
  $p = Start-Process cmd.exe -ArgumentList '/c', $cmd -PassThru -WindowStyle Hidden
  if (-not $p.WaitForExit(120000)) { Log '    timed out at 120s (killing)'; $p.Kill() }
  Log ("    exit code: {0}" -f $p.ExitCode)
  $deadline = (Get-Date).AddSeconds($MaxWaitSec)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    if (@(Get-TargetEntries -Patterns $name).Count -eq 0) { Log "    OK: $name removed"; return }
  }
  Log "    ?? $name still listed after wait"
}

Log '===== CHUNK B BEGIN (user-scope) ====='
# kill processes for these apps first
foreach ($n in @('Kimi','ZCode','Qoder','Kiro','KiroProxy','OpenCode','OmniRush','OpenClaw.Tray.WinUI','opera','opera_autoupdate','Opera','bun','steamwebhelper','steam','steamservice')) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Get-Process bun -ErrorAction SilentlyContinue | Where-Object { $_.Path -notlike '*@codebufffreebuff-desktop*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2

foreach ($pat in @('Kimi','OmniRush*','ZCode*','Kiro (User)','Qoder (User)','OpenCode*','OpenClaw*','Opera*','Bun','SteamUnlocked*')) {
  foreach ($e in @(Get-TargetEntries -Patterns @($pat))) {
    if ($e.PSPath -match 'HKLM' -and $e.PSPath -notmatch 'WOW6432') { Log "    SKIP (machine-scope, later chunk): $($e.DisplayName)"; continue }
    Invoke-UninstallEntry -e $e
  }
}
Log '===== CHUNK B END ====='
