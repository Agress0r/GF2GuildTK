@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Guild Tracker - Setup

cd /d "%~dp0"

if not exist "setup.ps1" (
    echo.
    echo ERROR: setup.ps1 not found.
    echo Make sure setup.bat is in the project root folder.
    echo.
    pause
    exit /b 90
)

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Windows PowerShell was not found.
    echo This installer supports Windows 10/11 and requires PowerShell.
    echo.
    pause
    exit /b 91
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "SETUP_RC=%ERRORLEVEL%"

echo.
if "%SETUP_RC%"=="0" (
    echo Setup finished successfully.
) else (
    echo Setup failed with exit code %SETUP_RC%.
    echo See setup.log in this folder for details.
)
echo.
pause
exit /b %SETUP_RC%
