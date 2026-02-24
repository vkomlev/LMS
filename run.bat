@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "ACTIVATE="
if exist ".venv\Scripts\activate.bat" set "ACTIVATE=.venv\Scripts\activate.bat"
if exist "venv\Scripts\activate.bat"     set "ACTIVATE=venv\Scripts\activate.bat"

if not defined ACTIVATE (
    echo Создание виртуальной среды...
    python -m venv venv
    if errorlevel 1 (
        echo Ошибка: не удалось создать venv. Проверьте, что Python установлен и доступен в PATH.
        pause
        exit /b 1
    )
    set "ACTIVATE=venv\Scripts\activate.bat"
)

echo Активация виртуальной среды...
call "%ACTIVATE%"

if exist "requirements.txt" (
    echo Проверка зависимостей...
    pip install -q -r requirements.txt
)

echo Запуск приложения...
python run.py

pause
