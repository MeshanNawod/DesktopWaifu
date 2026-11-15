@echo off
REM One-time setup: creates .venv and installs dependencies.
cd /d "%~dp0"

set PY=
for %%V in (3.13 3.12 3.11) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set PY=py -%%V
    )
)
if not defined PY set PY=py -3

echo Using: %PY%
%PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :fail

echo.
echo Setup complete. Start her with run.bat
pause
exit /b 0

:fail
echo.
echo Setup failed. Install Python 3.12 or 3.13 from python.org and try again.
pause
exit /b 1
