Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -and $cmd.Length -gt 140) { $cmd = $cmd.Substring(0,140) }
    "{0}  {1}  {2}" -f $_.ProcessId, $_.CreationDate, $cmd
}
