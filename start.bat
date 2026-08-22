@echo off
setlocal
rem ============================================================
rem  TaskLight launcher
rem  - Keep this file next to tasklight.py, or
rem  - Run install.bat once to create a Desktop launcher.
rem ============================================================
set "DIR=%~dp0"
if exist "%DIR%tasklight.py" goto :run
rem __TASKLIGHT_DIR__ is replaced by install.bat with the real path:
set "DIR=__TASKLIGHT_DIR__"
:run
if not exist "%DIR%tasklight.py" (
    echo [TaskLight] tasklight.py not found.
    echo Option A: keep this bat in the same folder as tasklight.py.
    echo Option B: run install.bat once to generate a Desktop launcher.
    pause
    exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
    echo [TaskLight] Python not found in PATH. Install Python 3.8+ first.
    pause
    exit /b 1
)
python -c "import psutil" >nul 2>nul
if errorlevel 1 (
    echo [TaskLight] Installing psutil ...
    python -m pip install psutil
)
python "%DIR%tasklight.py" %*