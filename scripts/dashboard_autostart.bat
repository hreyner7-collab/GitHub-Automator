@echo off
REM Launcher for the supervised dashboard. Kept in the repo so the Startup entry
REM is a thin pointer to real, versioned code instead of a copy that drifts.
REM
REM Found via %~dp0 (this file's own folder), so it works from any checkout path.
cd /d "%~dp0.."
if not exist "artifacts" mkdir "artifacts"
".venv\Scripts\python.exe" "scripts\dashboard_supervisor.py" >> "artifacts\dashboard.log" 2>&1
