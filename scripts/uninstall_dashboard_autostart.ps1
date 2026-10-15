# Removes the per-user "start the dashboard with Windows" entry.
# It does not stop a dashboard that is already running -- use the supervisor's
# --status to see it, or stop that process yourself.

$ErrorActionPreference = 'Stop'

$startup = [Environment]::GetFolderPath('Startup')
$entry   = Join-Path $startup 'Creality-Dashboard.vbs'

if (Test-Path $entry) {
  Remove-Item $entry -Force
  Write-Host "removed: $entry"
} else {
  Write-Host "nothing to remove: $entry does not exist"
}
