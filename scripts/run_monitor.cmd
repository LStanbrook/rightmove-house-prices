@echo off
REM Entry point for Windows Task Scheduler. Runs a full daily cycle and
REM writes a dated log under data\logs\.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [error] .venv not found - run:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

if not exist "data\logs" mkdir "data\logs"
REM Locale-independent yyyy-MM-dd for the log filename.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TODAY=%%d"

".venv\Scripts\python.exe" -m rightmove_monitor.cli all >> "data\logs\monitor_%TODAY%.log" 2>&1
echo Done. See data\logs\monitor_%TODAY%.log
endlocal
