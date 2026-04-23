# AI Contract — LMS

## Проект
- Имя: LMS Core API
- Стек: Python 3.10+, FastAPI, SQLAlchemy 2.x (async), PostgreSQL, Alembic
- Архитектурный порядок слоёв: `api → services → repos → models`

## Назначение
Обязательные правила для AI-агентов, работающих в этом репозитории. Документ не описывает «что делает проект» — для этого есть [architecture.md](architecture.md). Здесь только процессные правила.

## Контекстные уровни
- **minimal** — рутина: форматирование, мелкие правки, boilerplate, коммиты
- **standard** — спецификации, средние фичи, review, ТЗ
- **full** — архитектурные изменения, БД, миграции, сложная отладка, финальный quality-gate

Глобальная маршрутизация — в `~/.claude/CLAUDE.md`.

## Project Overrides
Проектные отклонения и ограничения — в [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md). При конфликте с глобальными правилами приоритет у overrides.

## Непереговорные правила
1. Не выполнять деструктивные действия без явного запроса.
2. Все изменения проверяемы: команды, тесты, логи, артефакты.
3. Интеграция в `main` (merge или прямой commit) — **только через `review-gate` PASS**.
4. Схема БД — только через Alembic миграции.
5. Секреты не писать в код, логи, коммиты, документацию.
6. Date/time сравнения — через helper/type-guard, raw `str` vs `datetime` запрещён.

## Обязательный цикл работы
1. **spec-gate** — формализация задачи и критериев готовности (`/spec-writer`).
2. **execution-gate** — реализация + minimal smoke (`/executor-lite` или `/executor-pro` или `/fastapi-api-developer`).
3. **review-gate** — независимый PASS/FAIL до merge в `main` (`/review-gate` или `/techlead-code-reviewer`).
4. **merge-gate** — интеграция только при PASS.

## Артефакты по задаче

Для каждой задачи на выходе должны быть:

- План и критерии готовности (spec)
- Список изменённых файлов (с diff)
- Результаты проверок (smoke/tests/log-triage)
- Review-артефакт: `reviews/YYYY-MM-DD-краткое-описание.{md,diff}`
- Решение review-gate: PASS/FAIL + замечания

## Куда сохранять документацию

Мапа «скилл → путь» зафиксирована в `.claude/CLAUDE.md` (раздел «Documentation paths»). При расхождениях — CLAUDE.md — истина.

## Связанные файлы AI-слоя
- [architecture.md](architecture.md) — компоненты, слои, потоки данных
- [data-model.md](data-model.md) — сущности БД, триггеры, миграции
- [glossary.md](glossary.md) — доменные термины
- [ERRORS.md](ERRORS.md) — журнал инцидентов
- [WORKFLOWS/](WORKFLOWS/) — шаблоны feature/bugfix/db-change
- [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md) — проектные ограничения

## Политика ветвления
- Основной режим: прямая работа в `main`.
- Отдельные ветки — только для автономных длительных запусков без участия человека.
- При работе в `main`: усиленный контроль (релевантные тесты, smoke, review-gate) **до** commit.
