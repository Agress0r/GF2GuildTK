@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "GF2TTK\Scripts\python.exe" (
    echo.
    echo ERROR: Virtual environment not found. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

"GF2TTK\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 42)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: This venv is not Python 3.12.x. Run setup.bat again.
    echo.
    pause
    exit /b 2
)

echo Starting Guild Tracker...
"GF2TTK\Scripts\python.exe" main.py
set "APP_RC=%ERRORLEVEL%"
if not "%APP_RC%"=="0" (
    echo.
    echo ERROR: Application exited with code %APP_RC%.
    echo Check setup.log or run from cmd to see Python errors.
    echo.
    pause
)
exit /b %APP_RC%
