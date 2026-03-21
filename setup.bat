@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Guild Tracker - Setup

:: ---- Ensure working directory is script location ----
cd /d "%~dp0"

echo ============================================================
echo   Guild Tracker - Environment Setup
echo ============================================================
echo.

:: ---- Preflight checks ----
if not exist "requirements.txt" (
    echo  ERROR: requirements.txt not found.
    echo  Make sure setup.bat is in the project root folder.
    echo.
    pause
    exit /b 1
)

if not exist "main.py" (
    echo  ERROR: main.py not found.
    echo  Make sure setup.bat is in the project root folder.
    echo.
    pause
    exit /b 1
)

:: ---- Step 1: Find Python 3.12+ ----
echo [1/5] Checking Python...
set PYTHON_EXE=
set BAD_VER=

:: Try py launcher with specific versions (3.13, 3.12)
call :try_py_launcher 3.13
if defined PYTHON_EXE goto :found_python
call :try_py_launcher 3.12
if defined PYTHON_EXE goto :found_python

:: Try 'python3' command
call :try_python_cmd python3
if defined PYTHON_EXE goto :found_python

:: Try 'python' command (may be Windows Store redirect — returns errorlevel 1, handled)
call :try_python_cmd python
if defined PYTHON_EXE goto :found_python

:: Nothing worked
if defined BAD_VER goto :bad_python
goto :no_python

:bad_python
echo.
echo  ERROR: Python !BAD_VER! found, but 3.12+ is required.
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
echo    2. IMPORTANT: Check "Add Python to PATH" during installation
echo    3. Close and re-open this terminal
echo    4. Re-run setup.bat
echo.
pause
exit /b 1

:found_python
for /f "tokens=2 delims= " %%V in ('!PYTHON_EXE! --version 2^>^&1') do set PYVER=%%V
echo  Python !PYVER! [!PYTHON_EXE!] - OK

:: ---- Step 2: Virtual environment ----
echo.
echo [2/5] Creating virtual environment GF2TTK\...

if exist "GF2TTK\Scripts\python.exe" (
    :: Validate existing venv actually works
    GF2TTK\Scripts\python.exe -c "import sys; sys.exit(0)" >nul 2>&1
    if not errorlevel 1 (
        echo  Already exists - skipping.
        goto :venv_done
    )
    echo  Existing venv is broken, recreating...
    rmdir /s /q GF2TTK >nul 2>&1
)

:: Check venv module is available
!PYTHON_EXE! -c "import venv" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python 'venv' module is not available.
    echo  Reinstall Python and check "Install for all users".
    echo.
    pause
    exit /b 1
)

!PYTHON_EXE! -m venv GF2TTK
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to create virtual environment.
    echo  Try deleting the GF2TTK folder and running setup.bat again.
    echo.
    pause
    exit /b 1
)
echo  Done.

:venv_done

:: ---- Step 3: Upgrade pip ----
echo.
echo [3/5] Upgrading pip...
GF2TTK\Scripts\python.exe -m pip install --upgrade pip --quiet >nul 2>&1
if errorlevel 1 (
    echo  Warning: pip upgrade failed. Continuing with existing version...
) else (
    echo  Done.
)

:: ---- Step 4: Install dependencies ----
echo.
echo [4/5] Installing dependencies (may take a few minutes)...
GF2TTK\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: Failed to install dependencies.
    echo.
    echo  Possible causes:
    echo    - No internet connection
    echo    - Firewall or proxy blocking pip
    echo    - Antivirus interfering with installation
    echo.
    echo  Try again. If the problem persists, run manually:
    echo    GF2TTK\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo  Dependencies installed.

:: ---- Step 5: Warm up OCR models ----
echo.
echo [5/5] Pre-loading OCR models...
GF2TTK\Scripts\python.exe -c "import os; os.environ['ORT_LOGGING_LEVEL']='3'; import ddddocr; ddddocr.DdddOcr(show_ad=False); from rapidocr_onnxruntime import RapidOCR; RapidOCR()" >nul 2>&1
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

:: ---- Done ----
echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo  To run the app:
echo    run.bat
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
exit /b 0

:: ============================================================
::  Subroutines (below exit /b 0, never reached by fall-through)
:: ============================================================

:: ---- Try py launcher with a specific version ----
:: Usage: call :try_py_launcher 3.13
:try_py_launcher
py -%~1 --version >nul 2>&1
if errorlevel 1 exit /b
:: Validate output is a real version (py launcher may return 0 with error text)
for /f "tokens=2 delims= " %%A in ('py -%~1 --version 2^>^&1') do set _PY_CHECK=%%A
echo !_PY_CHECK! | findstr /r "^[0-9]" >nul 2>&1
if errorlevel 1 exit /b
set PYTHON_EXE=py -%~1
exit /b

:: ---- Try a python command and validate version >= 3.12 ----
:: Usage: call :try_python_cmd python
:try_python_cmd
%~1 --version >nul 2>&1
if errorlevel 1 exit /b
set _PY_VER=
for /f "tokens=2 delims= " %%V in ('%~1 --version 2^>^&1') do set _PY_VER=%%V
:: Must look like a version number
echo !_PY_VER! | findstr /r "^[0-9]" >nul 2>&1
if errorlevel 1 exit /b
:: Parse major.minor
set _PY_MAJ=
set _PY_MIN=
for /f "tokens=1 delims=." %%A in ("!_PY_VER!") do set _PY_MAJ=%%A
for /f "tokens=2 delims=." %%B in ("!_PY_VER!") do set _PY_MIN=%%B
if not defined _PY_MAJ exit /b
if not defined _PY_MIN exit /b
:: Version < 3.x — too old
if !_PY_MAJ! LSS 3 ( set BAD_VER=!_PY_VER! & exit /b )
:: Version 4+ — accept
if !_PY_MAJ! GTR 3 ( set PYTHON_EXE=%~1 & exit /b )
:: Version 3.x where x < 12 — too old
if !_PY_MIN! LSS 12 ( set BAD_VER=!_PY_VER! & exit /b )
:: Version 3.12+ — accept
set PYTHON_EXE=%~1
exit /b
