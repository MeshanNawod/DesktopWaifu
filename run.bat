@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
REM pythonw = no console window. Use "run.bat debug" to see logs.
if /i "%1"=="debug" (
    .venv\Scripts\python.exe main.py
    pause
) else (
    start "" .venv\Scripts\pythonw.exe main.py
)
