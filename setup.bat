@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Guild Tracker — Установка

echo ============================================================
echo   Guild Tracker — Установка окружения
echo ============================================================
echo.

:: ---- Шаг 1: Проверка Python ----
echo [1/5] Проверяем Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ОШИБКА: Python не найден в PATH.
    echo.
    echo  Скачайте Python 3.12 или новее:
    echo    https://www.python.org/downloads/
    echo.
    echo  При установке обязательно отметьте "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJ=%%a
    set PYMIN=%%b
)
if !PYMAJ! LSS 3 goto :bad_python
if !PYMAJ! EQU 3 if !PYMIN! LSS 12 goto :bad_python
echo  Python !PYVER! — OK
goto :after_python_check

:bad_python
echo.
echo  ОШИБКА: Требуется Python 3.12+. У вас: !PYVER!
echo.
pause
exit /b 1

:after_python_check

:: ---- Шаг 2: Виртуальное окружение ----
echo.
echo [2/5] Создаём виртуальное окружение GF2TTK\...
cd /d "%~dp0"

if exist "GF2TTK\Scripts\python.exe" (
    echo  Уже существует — пропускаем создание.
) else (
    python -m venv GF2TTK
    if errorlevel 1 (
        echo.
        echo  ОШИБКА: Не удалось создать виртуальное окружение.
        echo.
        pause
        exit /b 1
    )
    echo  Создано.
)

:: ---- Шаг 3: Обновление pip ----
echo.
echo [3/5] Обновляем pip...
GF2TTK\Scripts\python.exe -m pip install --upgrade pip --quiet
echo  Готово.

:: ---- Шаг 4: Установка зависимостей ----
echo.
echo [4/5] Устанавливаем зависимости (может занять несколько минут)...
GF2TTK\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ОШИБКА: Не удалось установить зависимости.
    echo  Проверьте интернет-соединение и повторите попытку.
    echo.
    pause
    exit /b 1
)
echo  Зависимости установлены.

:: ---- Шаг 5: Прогрев OCR-моделей ----
echo.
echo [5/5] Прогрев OCR-моделей (первый запуск приложения будет быстрее)...
GF2TTK\Scripts\python.exe -c "import os; os.environ['ORT_LOGGING_LEVEL']='3'; import ddddocr; ddddocr.DdddOcr(show_ad=False); from rapidocr_onnxruntime import RapidOCR; RapidOCR()" 2>nul
if errorlevel 1 (
    echo  Предупреждение: прогрев не удался. Модели загрузятся при первом запуске приложения.
) else (
    echo  OCR-модели готовы.
)

:: ---- Создать debug_crops\ если нет ----
if not exist "debug_crops\" mkdir debug_crops

:: ---- Итог ----
echo.
echo ============================================================
echo   Установка завершена успешно!
echo ============================================================
echo.
echo  Запуск приложения:
echo    scripts\run.bat
echo.
echo  При первом запуске нужно:
echo    1. Создать сезон (кнопка "Сезоны")
echo    2. Откалибровать зоны захвата (кнопка "Калибровка")
echo.
echo  Для работы с Google Sheets:
echo    Поместите credentials.json в корень папки проекта.
echo    Подробнее — в README.md, раздел "Google Sheets".
echo.
pause
