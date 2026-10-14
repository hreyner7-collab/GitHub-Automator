foreach ($f in @('cleanup-main.ps1','cleanup-bootstrap.ps1','chunk-a.ps1','chunk-b.ps1','chunk-c.ps1','chunk-d.ps1','chunk-e.ps1')) {
  $p = Join-Path $PSScriptRoot $f
  $errs = $null
  [System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errs) | Out-Null
  if ($errs -and $errs.Count -gt 0) {
    Write-Host "$f : $($errs.Count) PARSE ERRORS"
    $errs | ForEach-Object { Write-Host ("  line {0}: {1}" -f $_.Extent.StartLineNumber, $_.Message) }
  } else {
    Write-Host "$f : OK"
  }
}
