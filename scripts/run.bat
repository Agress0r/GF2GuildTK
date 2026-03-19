@echo off
cd /d "%~dp0.."

if not exist "GF2TTK\Scripts\python.exe" (
    echo.
    echo  ОШИБКА: Виртуальное окружение не найдено.
    echo.
    echo  Сначала запустите установку:
    echo    setup.bat
    echo.
    pause
    exit /b 1
)

echo Запуск Guild Tracker...
GF2TTK\Scripts\python.exe main.py
