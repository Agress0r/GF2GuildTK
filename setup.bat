@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Guild Tracker - Setup

echo ============================================================
echo   Guild Tracker - Environment Setup
echo ============================================================
echo.

:: ---- Step 1: Find Python 3.12+ ----
echo [1/5] Checking Python...
set PYTHON_EXE=

:: Try py -3.13 (Python Launcher)
py -3.13 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_EXE=py -3.13 & goto :found_python )

:: Try py -3.12 (Python Launcher)
py -3.12 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_EXE=py -3.12 & goto :found_python )

:: Try 'python' - check version manually
python --version >nul 2>&1
if errorlevel 1 goto :no_python
for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PYVER=%%V
for /f "tokens=1 delims=." %%A in ("!PYVER!") do set PYMAJ=%%A
for /f "tokens=2 delims=." %%B in ("!PYVER!") do set PYMIN=%%B
if not defined PYMAJ goto :no_python
if not defined PYMIN goto :no_python
if !PYMAJ! LSS 3 goto :bad_python
if !PYMAJ! GTR 3 ( set PYTHON_EXE=python & goto :found_python )
if !PYMIN! LSS 12 goto :bad_python
set PYTHON_EXE=python
goto :found_python

:bad_python
echo.
echo  ERROR: Python !PYVER! found, but 3.12+ is required.
echo  Please install Python 3.12 or newer: https://www.python.org/downloads/
echo  Make sure to check "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:no_python
echo.
echo  ERROR: Python 3.12+ not found.
echo.
echo  What to do:
echo    1. Download Python 3.12+: https://www.python.org/downloads/
echo    2. Check "Add Python to PATH" during installation
echo    3. Re-run this script
echo.
pause
exit /b 1

:found_python
for /f "tokens=2 delims= " %%V in ('!PYTHON_EXE! --version 2^>^&1') do set PYVER=%%V
echo  Python !PYVER! [!PYTHON_EXE!] - OK

:: ---- Step 2: Virtual environment ----
echo.
echo [2/5] Creating virtual environment GF2TTK\...
cd /d "%~dp0"

if exist "GF2TTK\Scripts\python.exe" (
    echo  Already exists - skipping.
    goto :venv_done
)

!PYTHON_EXE! -m venv GF2TTK
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to create virtual environment.
    echo.
    pause
    exit /b 1
)
echo  Done.

:venv_done

:: ---- Step 3: Upgrade pip ----
echo.
echo [3/5] Upgrading pip...
GF2TTK\Scripts\python.exe -m pip install --upgrade pip --quiet
echo  Done.

:: ---- Step 4: Install dependencies ----
echo.
echo [4/5] Installing dependencies (may take a few minutes)...
GF2TTK\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to install dependencies.
    echo  Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)
echo  Dependencies installed.

:: ---- Step 5: Warm up OCR models ----
echo.
echo [5/5] Pre-loading OCR models...
GF2TTK\Scripts\python.exe -c "import os; os.environ['ORT_LOGGING_LEVEL']='3'; import ddddocr; ddddocr.DdddOcr(show_ad=False); from rapidocr_onnxruntime import RapidOCR; RapidOCR()" 2>nul
if errorlevel 1 (
    echo  Warning: pre-load failed. Models will load on first app launch.
) else (
    echo  OCR models ready.
)

:: ---- Create debug_crops folder ----
if not exist "debug_crops\" mkdir debug_crops

:: ---- Create run.bat in root ----
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo.
    echo if not exist "GF2TTK\Scripts\python.exe" ^(
    echo     echo.
    echo     echo  ERROR: Virtual environment not found. Run setup.bat first.
    echo     echo.
    echo     pause
    echo     exit /b 1
    echo ^)
    echo.
    echo GF2TTK\Scripts\python.exe main.py
) > "run.bat"
echo  run.bat created in project root.

:: ---- Done ----
echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo  To run the app:
echo    scripts\run.bat
echo.
echo  First launch steps:
echo    1. Create a season (button "Seasons")
echo    2. Calibrate capture zones (button "Calibration")
echo.
echo  For Google Sheets integration:
echo    Place credentials.json in the project root folder.
echo    See README.md for details.
echo.
pause
