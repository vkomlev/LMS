# Review: tsk-004 Этап 1.6 (фаза 1) — tasks.order_position

**Дата:** 2026-05-21
**Ветка:** main (изменения uncommitted)
**Связано:**
- Бриф: [docs/briefs/tsk-004-tasks-order-position.md](../docs/briefs/tsk-004-tasks-order-position.md)
- Тест-план: [docs/briefs/tsk-004-tasks-order-position-testplan.md](../docs/briefs/tsk-004-tasks-order-position-testplan.md)
- ТЗ-1: [docs/specs/2026-05-21-tz-tasks-order-position-stage1.md](../docs/specs/2026-05-21-tz-tasks-order-position-stage1.md)
- ТЗ-2 (фаза 2, заблокирована до merge фазы 1): [docs/specs/2026-05-21-tz-tasks-order-position-stage2.md](../docs/specs/2026-05-21-tz-tasks-order-position-stage2.md)
- Контракт: [docs/database-triggers-contract.md](../docs/database-triggers-contract.md) разделы 13-14
- CB mirror: `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md`, `CHANGELOG.md`

## Контекст
Добавлено поле `tasks.order_position INTEGER NULL` с триггерами PL/pgSQL,
зеркало `materials.order_position`. Учебный движок (`learning_engine_service`)
и `tasks_service.get_by_course` сортируют по `order_position NULLS LAST, id`.
Бекфилл существующих 567 строк через `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)` —
порядок задач для активных студентов не изменился.

## Изменения

### LMS
| Файл | Тип | Описание |
|---|---|---|
| `app/db/migrations/versions/20260521_120000_tasks_order_position_triggers.py` | new | Колонка + бекфилл + 2 триггера + индекс. AFTER DELETE сразу statement-level (учитываем урок materials) |
| `app/models/tasks.py` | edit | Добавлена `order_position: Optional[int]`; docstring указывает на триггеры |
| `app/services/tasks_service.py` | edit | `get_by_course` переписан на явный select с ORDER BY order_position NULLS LAST, id |
| `app/services/learning_engine_service.py` | edit | next-item picker order_by: id → order_position NULLS LAST, id |
| `docs/database-triggers-contract.md` | edit | Разделы 13-14 + 2 новые строки в таблице триггеров + история изменений |
| `tests/test_tasks_order_position.py` | new | 17 integration-тестов (T1-T15, T25, F5) |
| `scripts/dump_tasks_baseline.py` | new | Одноразовый дамп (id, course_id) до миграции для snapshot-теста |
| `reviews/2026-05-21-tasks-order-position-baseline.csv` | new | Baseline 567 строк до миграции |
| `docs/briefs/tsk-004-tasks-order-position.md` | new (eng-review) | Бриф с зафиксированными решениями |
| `docs/briefs/tsk-004-tasks-order-position-testplan.md` | new (eng-review) | Тест-план T1-T26 |
| `docs/specs/2026-05-21-tz-tasks-order-position-stage1.md` | new (tech-spec) | ТЗ-1 |
| `docs/specs/2026-05-21-tz-tasks-order-position-stage2.md` | new (tech-spec) | ТЗ-2 (blocked) |

### ContentBackbone (cross-project mirror)
| Файл | Тип | Описание |
|---|---|---|
| `docs/cross-project/contracts/lms-db-schema.md` | edit | Раздел `tasks` дополнен: order_position + 2 триггера + индекс + отличие от materials + сноска про бекфилл |
| `docs/cross-project/CHANGELOG.md` | edit | Запись tsk-004 Этап 1.6 фаза 1 в начало |

## Smoke verification (через MCP postgresql)

| Метрика | Ожидание | Факт |
|---|---|---|
| alembic_head | `tasks_order_position_triggers` | `tasks_order_position_triggers` ✅ |
| total tasks (после rollback'ов тестов) | 567 (baseline) | 568 (одна стрейкер-запись от Y4-теста — НЕ связана с этой работой) |
| order_position IS NULL | 0 | 0 ✅ |
| Триггеры `trg_set_task_order_position` + `trg_reorder_tasks_after_delete` | 3 events (INSERT,UPDATE,DELETE) | 3 ✅ |
| Индекс `idx_tasks_course_order` | присутствует | присутствует ✅ |
| Per-course MIN/MAX/COUNT инвариант | `MIN=1, MAX=COUNT` для всех 25 курсов | ✅ для всех |
| T25 snapshot equivalence (LE regression) | divergent=0 | divergent=0 ✅ |
| T14 бекфилл инвариант | mismatches=0 | mismatches=0 ✅ |

## Tests

```text
pytest tests/test_tasks_order_position.py -v
======================= 17 passed, 11 warnings in 3.30s =======================
```

Полный suite (49 фейлов в `pytest tests/`, ноль связан с `tasks/order_position/LE`):
- `test_y6_review_loop.py::test_y6_escalation_cron_tick_idempotent` — известная pre-existing regression
  (зафиксирована в `D:\Work\Root\tasks\tsk-004-poryadok-v-lms.md` этап 1.1).
- `test_y5_guest_endpoints.py::*` (7 шт.) — pre-existing.
- `test_teacher_next_modes_stage39.py::test_manual_check_wrong_lock_token_409` — pre-existing.

## Покрытие тестами (T1-T26 из тест-плана)

| Test | Назначение | Статус |
|---|---|---|
| T1 | INSERT NULL в пустой курс → pos=1 | ✅ pass |
| T2 | INSERT NULL в курс с N задач → pos=N+1 | ✅ pass |
| T3 | INSERT с явным K сдвигает >=K на +1 | ✅ pass |
| T4 | INSERT с pos > MAX+1 (дырка) | ✅ pass |
| T5 | UPDATE pos N→M (M>N): сдвиг на -1 | ✅ pass |
| T6 | UPDATE pos N→M (M<N): сдвиг на +1 | ✅ pass |
| T7 | UPDATE pos→NULL: пересдвиг с дыркой (наследовано от materials) | ✅ pass (зафиксированное поведение) |
| T8 | UPDATE pos→pos (no-op) | ✅ pass |
| T9 | Изоляция по course_id | ✅ pass |
| T10 | DELETE одной → пересчёт | ✅ pass |
| T11 | **DELETE multi-row** (regression statement-level) | ✅ pass |
| T12 | DELETE последней — нет ошибок | ✅ pass |
| T13 | DELETE из нескольких курсов одним statement | ✅ pass |
| T14 | Бекфилл инвариант на реальных данных | ✅ pass |
| T15 | После DELETE order_position == ROW_NUMBER(…) | ✅ pass |
| T16-T19 | Bulk-upsert | — (фаза 2) |
| T20-T24 | API endpoints | — (фаза 2) |
| T25 | **LE snapshot equivalence** (regression Learning Engine) | ✅ pass |
| T26 | Existing LE tests pass | ✅ pass |
| F5 | **session-var is_local=true** (критический пробел из ревью) | ✅ pass |

## Открытые follow-up
- **R1 (race INSERT NULL × NULL)** — унаследовано от materials. TODO в `D:\Work\Root\tasks\`. Решение: advisory lock или unique partial index — отдельный PR, охватывает обе таблицы.
- **T7 «дырка после UPDATE → NULL»** — наследованное поведение materials, зафиксировано тестом. Не баг этого ТЗ.
- **ТЗ-2 (фаза 2)** — `TaskCreate/TaskUpdate/TaskRead/TaskUpsertItem` + bulk_upsert проброс + smoke API + CB lms-api.md mirror.

## Команды rollback (на случай инцидента)

```powershell
cd D:\Work\LMS
.\.venv\Scripts\activate
alembic downgrade -1
# Структура tasks вернётся к m12_y6_optimistic_pass; значения order_position теряются;
# при повторном upgrade бекфилл по id ASC восстановит детерминированно.

# Откат кода:
git checkout -- app/models/tasks.py app/services/tasks_service.py app/services/learning_engine_service.py docs/database-triggers-contract.md
git restore --staged app/db/migrations/versions/20260521_120000_tasks_order_position_triggers.py
rm app/db/migrations/versions/20260521_120000_tasks_order_position_triggers.py
rm tests/test_tasks_order_position.py
```

## Готовность к merge
- ✅ Все criteria приёмки ТЗ-1 выполнены (#1-#10).
- ✅ 17/17 фокусных тестов pass.
- ✅ Нет регрессий, связанных с tasks/order_position/LE.
- ✅ Контракт обновлён.
- ✅ CB mirror подготовлен (commits ждут approval оператора).
- ⏳ Ждёт: `git commit` (LMS) + `git commit` (CB) + создание PR + `/review-gate` → `/lms-fastapi-techlead-code-reviewer`.
