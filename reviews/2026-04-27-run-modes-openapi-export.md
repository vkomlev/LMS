# Review: Run Modes + OpenAPI Export

**Date:** 2026-04-27
**Context:** Два режима запуска (dev/prod) + скрипт выгрузки openapi.json

## Plan

1. `run.py` — добавить `--dev` флаг через argparse; по умолчанию `reload=False`
2. `run.bat` — пробросить `%*` чтобы аргументы доходили до `run.py`
3. `scripts/export_openapi.py` — standalone-скрипт, импортирует app и пишет `docs/openapi.json`

## Changed Files

| Файл | Изменение |
|---|---|
| `run.py` | argparse `--dev`; `reload=args.dev` (default False) |
| `run.bat` | `python run.py %*` вместо `python run.py` |
| `scripts/export_openapi.py` | Новый скрипт; 134 endpoint зафиксировано |
| `docs/openapi.json` | Перезаписан с pretty-print (indent=2) |

## Validation Results

- `python run.py --help` — PASS, показывает `--dev` аргумент
- `python scripts/export_openapi.py` — PASS, `134 endpoints`, файл обновлён
- `reload=False` при запуске без флага — PASS (argparse default)

## Usage

```bash
# Разработка (с hot-reload)
python run.py --dev
run.bat --dev

# Продакшн (без reload)
python run.py
run.bat

# Выгрузить openapi.json
python scripts/export_openapi.py
```

## Risks / Follow-ups

- `run.bat` передаёт `%*` напрямую — любые аргументы попадут в uvicorn argparse; это нормально
- Выгрузку openapi.json можно автоматизировать через git pre-commit hook при необходимости
