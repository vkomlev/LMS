---
slug: tsk-004-tasks-order-position
title: Поле order_position для tasks (зеркало materials)
parent_task: tsk-004
status: finalized
created: 2026-05-21
updated: 2026-05-21
owner: eng-review
related_files:
  - app/models/tasks.py
  - app/schemas/tasks.py
  - app/repos/tasks_repo.py
  - app/services/tasks_service.py
  - app/services/learning_engine_service.py
  - app/api/v1/tasks_extra.py
  - app/db/migrations/versions/20260129_100000_materials_structure_and_triggers.py
  - app/db/migrations/versions/20260205_140000_fix_materials_delete_trigger.py
  - docs/database-triggers-contract.md
authority_docs:
  - docs/database-triggers-contract.md
  - D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md
  - D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md
---

# Бриф: order_position для tasks

## 1. Цель
Добавить поле `order_position` в таблицу `tasks` с бизнес-логикой 1-в-1 как у `materials`:
автоматическая нумерация при INSERT/UPDATE и пересчёт после DELETE через триггеры PL/pgSQL.

## 2. Контекст (что уже есть)
- Канон в [docs/database-triggers-contract.md](../database-triggers-contract.md) разделы 7-8.
- Шаблон-миграция [20260129_100000_materials_structure_and_triggers.py](../../app/db/migrations/versions/20260129_100000_materials_structure_and_triggers.py).
- Bugfix AFTER DELETE [20260205_140000_fix_materials_delete_trigger.py](../../app/db/migrations/versions/20260205_140000_fix_materials_delete_trigger.py) — FOR EACH STATEMENT обязателен.
- `tasks_repo.py` пуст (наследует BaseRepository).
- Ordering задач для Learning Engine идёт в [learning_engine_service.py:418-421](../../app/services/learning_engine_service.py#L418-L421) по `Tasks.id.asc()`.

## 3. Решение
- Колонка `order_position INTEGER NULL` (NULL = в конец).
- Два триггера: `trg_set_task_order_position` (BEFORE INSERT/UPDATE, FOR EACH ROW, со skip-config var) +
  `trg_reorder_tasks_after_delete` (AFTER DELETE, FOR EACH STATEMENT, REFERENCING OLD TABLE) — учтя баг materials сразу.
- Индекс `idx_tasks_course_order` на `(course_id, order_position NULLS LAST)`.
- Backfill для существующих строк до создания триггера.
- Сортировка по `order_position` подменяет сортировку по `id` в Learning Engine и `/tasks/by-course`.

## 4. Разделы ревью (зафиксированные решения)

### Архитектура (4)
- **1A1**. Атомарный PR: миграция + правка `learning_engine_service.py:418-421` (order_by `Tasks.order_position.asc().nulls_last(), Tasks.id`) + бекфилл по id ASC в той же транзакции.
- **1B1**. AFTER DELETE сразу FOR EACH STATEMENT + REFERENCING OLD TABLE (итоговая версия materials, не повторяем bug-fix цикл).
- **1C1**. Бекфилл: `UPDATE tasks SET order_position = ROW_NUMBER() OVER(PARTITION BY course_id ORDER BY id ASC)` под `app.skip_task_order_trigger='true'`. Идемпотентно.
- **1D1**. UNIQUE(external_uid) у tasks глобальный — оставить, явный комментарий в миграции и разделе 13-14 контракта о разнице с materials.

### Качество (3)
- **2A1**. Расширить `TaskUpsertItem` полем `order_position: Optional[int]`, проброс в `bulk_upsert.create/update` ветки.
- **2B1**. Переписать `TasksService.get_by_course` на явный `select(...).order_by(Tasks.order_position.asc().nulls_last(), Tasks.id)` вместо BaseService.paginate.
- **2C1**. `/tasks/search` оставить `order_by(Tasks.id)` — search кросс-курсовой.
- **2D** (без вопроса). Обновить docstring `app/models/tasks.py`: «Порядок показа (order_position) управляется триггерами БД».
- **2E** (без вопроса). PL/pgSQL `set_task_order_position` дублирует `set_material_order_position` на 95% — осознанное дублирование, generic-обёртка непрактична.

### Тесты (1 пробел)
- **3A1**. Integration-тест `test_tasks_order_position_backfill_invariant.py` с fixture «double apply»: создать tasks с известными id, сравнить ORDER BY id (до) и ORDER BY order_position NULLS LAST, id (после) — должны совпасть. Плюс 25 кейсов из test-plan (T1-T26).

### Производительность (2)
- **4A1**. `CREATE INDEX idx_tasks_course_order ON tasks (course_id, order_position NULLS LAST)` в той же миграции.
- **4B1**. Bulk-INSERT с явным возрастающим order_position остаётся O(N²) под триггером (как у materials). В разделе 13-14 контракта пометка: «для bulk-импорта рекомендуется слать `order_position=NULL`».

## 5. Out of scope
- Изменение UNIQUE(external_uid) на UNIQUE(course_id, external_uid) для tasks (расширение охвата).
- Fast-path для bulk-upsert через DISABLE TRIGGER (follow-up при росте tasks > 5k).
- Сортировка `/tasks/search` по `(course_id, order_position, id)`.
- Generic-обёртка `set_order_position(p_table, p_partition)` PL/pgSQL.
- Аналогичный механизм для других таблиц (course_dependencies, identity_link и т.п.).
- Изменение `order_number` у `user_courses`/`course_parents` — у них уже отдельные триггеры, не трогаем.

## 6. Что уже существует
| Существующее | Использование в плане |
|---|---|
| `set_material_order_position()` PL/pgSQL | Шаблон для `set_task_order_position()` — копировать тело, заменить `materials`→`tasks`, `app.skip_material_order_trigger`→`app.skip_task_order_trigger` |
| `reorder_materials_after_delete()` (statement-level, fix-версия) | Шаблон для `reorder_tasks_after_delete()` |
| Разделы 7-8 [docs/database-triggers-contract.md](../database-triggers-contract.md) | Шаблон для разделов 13-14 |
| Таблица в конце контракта (триггеры/ограничения) | Добавить 2 новые строки |
| `tests/test_triggers_smoke.py` | Расширить блоком для tasks (см. test-plan T1-T26) |
| `app/services/materials_service.py` create/update pass-through `order_position` | Шаблон для `tasks_service` пробрасывания |
| `learning_engine_service.py:383` materials ORDER BY | Шаблон для строки 418-421 tasks |

## 7. Risks & follow-ups
- **R1**. Race condition: два параллельных INSERT с NULL в один course_id могут получить одинаковый MAX+1 (`set_config` локальный для транзакции, но MAX считается до COMMIT). Materials живёт с этим — фиксируем как known-issue.
- **R2**. Cross-project sync обязателен: `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` + `lms-api.md` + CHANGELOG.md.
- **R3**. SPW (`D:\Work\spw\`) и TG_LMS (`D:\Work\TG_LMS\`) могут полагаться на текущий порядок tasks. После бекфилла по id ASC — порядок сохраняется, но любая ручная перестановка через UI/API будет видна потребителям.
- **R4**. Tests `test_tasks_*.py` могут содержать хардкод порядка id; проверить и обновить ожидания.
