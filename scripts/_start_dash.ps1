Start-Process -FilePath ".venv\Scripts\pythonw.exe" -ArgumentList "main.py" -WorkingDirectory (Get-Location) -WindowStyle Hidden
