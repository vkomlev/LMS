# docs/ai/ — AI-слой документации LMS

Точка входа для AI-агента. Сухие факты без нарратива. Назначение — ориентация в коде за минимальное число шагов.

## Карта

- [AGENTS.md](AGENTS.md) — Phase 0 контракт для AI-агентов (правила, гейты, артефакты)
- [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md) — проектные отклонения и ограничения
- [architecture.md](architecture.md) — компоненты, слои, потоки данных
- [data-model.md](data-model.md) — ключевые сущности БД, триггеры, миграции
- [glossary.md](glossary.md) — доменные термины (включая SPW + Auth + типы задач)
- [ERRORS.md](ERRORS.md) — журнал инцидентов и профилактика
- [operator-runbook.md](operator-runbook.md) — сценарии требующие оператора + autonomous workarounds
- [WORKFLOWS/](WORKFLOWS/) — шаблоны рабочих циклов
  - [feature.md](WORKFLOWS/feature.md)
  - [bugfix.md](WORKFLOWS/bugfix.md)
  - [db-change.md](WORKFLOWS/db-change.md)
- [adr/0001-auth-passwordless-multi-identity.md](adr/0001-auth-passwordless-multi-identity.md) — ADR: auth-стратегия Phase Y-1
- [design/teacher-queue-states.md](design/teacher-queue-states.md) — FSM очереди ручной проверки SA_COM

## Specs (исполняемые ТЗ)

- [../specs/2026-04-27-tech-spec-Y1-auth-extension.md](../specs/2026-04-27-tech-spec-Y1-auth-extension.md) — Tech-spec Y-1: миграции + auth (READY for executor)

## Когда читать какой файл

| Задача | Минимально нужно |
|---|---|
| Новая фича API | [architecture.md](architecture.md) + [WORKFLOWS/feature.md](WORKFLOWS/feature.md) |
| Багфикс | [WORKFLOWS/bugfix.md](WORKFLOWS/bugfix.md) + [ERRORS.md](ERRORS.md) |
| Изменение схемы БД | [data-model.md](data-model.md) + [WORKFLOWS/db-change.md](WORKFLOWS/db-change.md) |
| Новый агент, онбординг | [AGENTS.md](AGENTS.md) + [PROJECT_OVERRIDES.md](PROJECT_OVERRIDES.md) + [glossary.md](glossary.md) |
| Phase Y-1 (auth-расширение) | [specs/tech-spec-Y1](../specs/2026-04-27-tech-spec-Y1-auth-extension.md) + [adr/0001](adr/0001-auth-passwordless-multi-identity.md) |
| SA_COM / teacher review queue | [design/teacher-queue-states.md](design/teacher-queue-states.md) |

## Принципы обновления

- При изменении API-контрактов — обновлять [data-model.md](data-model.md) и соответствующий `docs/API_*.md`
- При изменении архитектуры (новые слои, интеграции) — обновлять [architecture.md](architecture.md)
- Новые инциденты с AI/smoke-провалами — добавлять запись в [ERRORS.md](ERRORS.md)
- Новые доменные термины — добавлять в [glossary.md](glossary.md)
