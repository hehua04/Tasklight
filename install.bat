@echo off
setlocal
rem Create a Desktop launcher (TaskLight.bat) that points to THIS folder
set "SRC=%~dp0"
if not exist "%SRC%tasklight.py" (
    echo [TaskLight] Please keep install.bat next to tasklight.py.
    pause
    exit /b 1
)
set "DESK="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%i"
if not defined DESK set "DESK=%USERPROFILE%\Desktop"
set "TL_DST=%DESK%\TaskLight.bat"
set "TL_SRC=%SRC%"
copy /y "%SRC%start.bat" "%TL_DST%" >nul 2>nul
if errorlevel 1 (
    echo [TaskLight] Failed to write %TL_DST%
    pause
    exit /b 1
)
powershell -NoProfile -Command "$c = Get-Content -LiteralPath $env:TL_DST -Raw; $c = $c.Replace('__TASKLIGHT_DIR__', $env:TL_SRC); Set-Content -LiteralPath $env:TL_DST -Value $c -Encoding Default"
echo [TaskLight] Desktop launcher created: %TL_DST%
echo Double-click it anytime to start TaskLight.
pause