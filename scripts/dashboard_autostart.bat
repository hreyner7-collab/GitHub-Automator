@echo off
REM Launcher for the supervised dashboard. Kept in the repo so the Startup entry
REM is a thin pointer to real, versioned code instead of a copy that drifts.
REM
REM Found via %~dp0 (this file's own folder), so it works from any checkout path.
REM
REM The console goes to its OWN file on purpose. Redirecting it to
REM artifacts\dashboard.log fails with "The process cannot access the file
REM because it is being used by another process" whenever a dashboard is already
REM running with that file as its stdout -- and cmd's >> is denied while Python's
REM own append handle is open. That made this launcher exit 1, silently, in a
REM hidden window, exactly when someone was trying to start a supervisor to watch
REM the dashboard that was already up.
cd /d "%~dp0.."
if not exist "artifacts" mkdir "artifacts"
".venv\Scripts\python.exe" "scripts\dashboard_supervisor.py" >> "artifacts\dashboard_autostart_console.log" 2>&1
