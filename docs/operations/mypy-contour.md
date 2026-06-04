# Воспроизводимый контур mypy

## Подготовка

Установить зависимости разработки в активное виртуальное окружение:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Запуск

```powershell
.venv\Scripts\python.exe scripts\run_mypy.py
```

Скрипт использует активный интерпретатор Python, конфигурацию `mypy.ini` и
проверяет модули импортного пути задач. Транзитивные ошибки соседних модулей
не входят в этот контур: для них требуется отдельное расширение baseline.

## Проверка из ТЗ импорта

Команда из ТЗ также использует ту же конфигурацию:

```powershell
.venv\Scripts\python.exe -m mypy --config-file mypy.ini app\services\tasks_service.py app\api\v1\tasks_extra.py
```
