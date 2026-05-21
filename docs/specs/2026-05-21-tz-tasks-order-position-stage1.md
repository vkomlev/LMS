---
id: tz-tasks-order-position-stage1
parent_task: tsk-004 (Этап 1.6, фаза 1)
created: 2026-05-21
status: ready-for-implementation
authority_brief: docs/briefs/tsk-004-tasks-order-position.md
authority_testplan: docs/briefs/tsk-004-tasks-order-position-testplan.md
authority_contract: docs/database-triggers-contract.md
blocks: tz-tasks-order-position-stage2
---

# ТЗ-1: tasks.order_position — миграция, триггеры, Learning Engine

## Цель
Добавить в таблицу `tasks` поле `order_position` с поведением, идентичным `materials`: автоматическая нумерация в БД через триггеры PL/pgSQL + согласованная сортировка в Learning Engine и `GET /tasks/by-course`. После применения миграции порядок выдачи задач для активных студентов **не изменится** (бекфилл по `id ASC`).

## Контекст
- Репозиторий: `D:\Work\LMS` (FastAPI, SQLAlchemy 2.x async, PostgreSQL 13+, Alembic).
- Затронутые модули: `app/db/migrations/versions`, `app/models/tasks.py`, `app/services/learning_engine_service.py`, `app/services/tasks_service.py`, `app/repos/tasks_repo.py`, `docs/database-triggers-contract.md`.
- Канон-шаблон для копирования: материалы — миграция [20260129_100000_materials_structure_and_triggers.py](../../app/db/migrations/versions/20260129_100000_materials_structure_and_triggers.py) + фикс [20260205_140000_fix_materials_delete_trigger.py](../../app/db/migrations/versions/20260205_140000_fix_materials_delete_trigger.py) + разделы 7-8 контракта.
- Текущее поведение LE: [learning_engine_service.py:418-421](../../app/services/learning_engine_service.py#L418-L421) — `select(Tasks.id).where(Tasks.course_id==…).order_by(Tasks.id.asc())`.
- Текущее поведение `TasksService.get_by_course`: [tasks_service.py:378-410](../../app/services/tasks_service.py#L378-L410) — `BaseService.paginate` **без** явного ORDER BY.
- Объём данных: ~567 строк в `tasks`, 161 курс, ~664 материала (sample 2026-05-21).

## Границы задачи

### Входит
- Alembic-миграция: ADD COLUMN `order_position INTEGER NULL`; CREATE FUNCTION `set_task_order_position()` + триггер BEFORE INSERT/UPDATE FOR EACH ROW; CREATE FUNCTION `reorder_tasks_after_delete()` + триггер AFTER DELETE FOR EACH STATEMENT (REFERENCING OLD TABLE); CREATE INDEX `idx_tasks_course_order`; UPDATE бекфилл `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)` под session-var.
- Правка модели `app/models/tasks.py` — добавить колонку и обновить docstring.
- Правка `learning_engine_service.py:418-421` — ORDER BY на `order_position NULLS LAST, id`.
- Правка `TasksService.get_by_course` — явный `select(...).where(...).order_by(Tasks.order_position.asc().nulls_last(), Tasks.id)`.
- Добавление разделов 13-14 в `docs/database-triggers-contract.md`.
- Cross-project mirror: `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-db-schema.md` (добавить триггеры, индекс, колонку); CHANGELOG.md.
- Integration-тесты (см. секцию «Команды проверки»): T1-T15, T25 из тест-плана.
- Регенерация `docs/openapi.json` после правок схем (фактически — без изменений API контракта в этой фазе).

### Не входит
- Расширение Pydantic-схем `TaskCreate/TaskUpdate/TaskRead/TaskUpsertItem` полем `order_position` — это **ТЗ-2**.
- `bulk_upsert` проброс `order_position` — ТЗ-2.
- Изменение `/tasks/search` сортировки — оставить `Tasks.id`.
- Изменение UNIQUE(external_uid) у tasks.
- Generic-обёртка PL/pgSQL для materials+tasks.
- Advisory lock против race INSERT NULL × NULL (открытая follow-up в TODOS).
- Любые правки SPW / TG_LMS клиентов.

### Не трогать
- Таблицы `materials`, `user_courses`, `course_parents`, `teacher_courses`, `course_dependencies` и их триггеры.
- `tests/test_triggers_smoke.py` для materials.
- `app/schemas/tasks.py` (это ТЗ-2).
- API роутер `app/api/v1/tasks_extra.py` — никаких сигнатур.

## Стек и ограничения
- Python 3.10+, FastAPI, SQLAlchemy 2.x async, Alembic.
- PostgreSQL 13+ (используются `REFERENCING OLD TABLE`, `ROW_NUMBER() OVER PARTITION BY` — доступны с 10+).
- Кодировка файлов UTF-8 без BOM.
- Коммиты: русский, императив, формат `<тип>: <описание>` (см. CLAUDE.md).
- Type hints обязательны, docstrings RU, `logging` вместо print.
- Без эмодзи в коде и логах.

## Обязательные скиллы/правила
- `/fastapi-api-developer` — основной исполнитель для миграции, моделей, сервисов, LE.
- `/db-check` — preflight read-only проверки до миграции и smoke после.
- `/executor-lite` — обновление контракта `docs/database-triggers-contract.md` и cross-project mirror.
- `/review-gate` — pre-merge независимый PASS/FAIL.
- `/lms-fastapi-techlead-code-reviewer` — итоговое ревью PR (БД-критичная миграция, date/raw SQL paths, migration safety).
- `~/.claude/CLAUDE.md` — глобальные стандарты.
- `d:\Work\LMS\CLAUDE.md` — Date/Time Safety, Review-changes обязательны.
- `docs/database-triggers-contract.md` — закон: бизнес-логика на уровне БД, не дублировать в коде.

## Шаги реализации

### 1. Preflight (до правок)
**Исполнитель:** `/db-check`
- Проверить `alembic current` и `alembic heads` — head должен быть после `fix_materials_delete_trigger`.
- MCP read-only: `SELECT COUNT(*) FROM tasks;` (зафиксировать N, ожидание ~567), `SELECT column_name FROM information_schema.columns WHERE table_name='tasks';` — убедиться, что `order_position` отсутствует.
- MCP: проверить, что в `tasks` нет orphan-строк с несуществующим `course_id` (если есть — БЛОКЕР, эскалация).
- MCP: проверить, что нет двух tasks с одинаковым (id) — sanity.
- Артефакт: фиксация baseline-снимка `(id, course_id)` всех tasks в файл `reviews/2026-05-21-tasks-order-position-baseline.csv` через psql `\copy` (для T25 backfill snapshot test).

### 2. Создание миграции
**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/lms-fastapi-techlead-code-reviewer` (migration safety, raw SQL paths)

Создать файл `app/db/migrations/versions/<TIMESTAMP>_tasks_order_position_triggers.py` (timestamp по локальному времени запуска).

`upgrade()`:

1. **ADD COLUMN.**
   ```python
   op.add_column(
       'tasks',
       sa.Column('order_position', sa.Integer(), nullable=True,
                 comment='Позиция в курсе (NULL = автоматически в конец)'),
   )
   ```

2. **Бекфилл (под session-var, чтобы триггер не появился раньше).** Триггер создаётся после бекфилла — session-var здесь формально не нужен (триггера ещё нет), но добавляем для идемпотентности при повторных миграциях:
   ```sql
   UPDATE tasks t
   SET order_position = rn.new_pos
   FROM (
       SELECT id,
              ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)::integer AS new_pos
       FROM tasks
   ) rn
   WHERE t.id = rn.id;
   ```

   **Mental trace** (verification by skill-routing-standard §14):
   - Курс=10, tasks id=[100,101,105] → ROW_NUMBER=[1,2,3] → order_position=[1,2,3] ✅
   - Курс=20, tasks id=[200] → ROW_NUMBER=[1] → order_position=[1] ✅
   - Курс=30, tasks=[] → нет строк, no-op ✅
   - Курс=40, tasks id=[400,402,404,406,408] → ROW_NUMBER=[1,2,3,4,5] → монотонно растущий по id ✅

3. **Индекс.**
   ```sql
   CREATE INDEX idx_tasks_course_order
   ON tasks (course_id, order_position NULLS LAST);
   ```

4. **PL/pgSQL функция `set_task_order_position`** — копия `set_material_order_position` из materials-миграции с заменой `materials`→`tasks`, `app.skip_material_order_trigger`→`app.skip_task_order_trigger`. **Сохранить третий параметр `set_config(..., true)` (is_local) для всех 5 вызовов.**

5. **Триггер BEFORE INSERT/UPDATE FOR EACH ROW** с guard:
   ```sql
   CREATE TRIGGER trg_set_task_order_position
       BEFORE INSERT OR UPDATE ON tasks
       FOR EACH ROW
       WHEN (current_setting('app.skip_task_order_trigger', true) IS DISTINCT FROM 'true')
       EXECUTE FUNCTION set_task_order_position();
   ```

6. **PL/pgSQL функция `reorder_tasks_after_delete`** — копия **finальной** (statement-level) версии из fix-миграции materials. Использует transition table `old_rows`. **НЕ копировать row-level вариант** — исторический баг materials.

7. **Триггер AFTER DELETE FOR EACH STATEMENT REFERENCING OLD TABLE.**

`downgrade()`:
- DROP TRIGGER + DROP FUNCTION для обоих триггеров.
- DROP INDEX.
- DROP COLUMN `order_position` (downgrade не пытается восстановить значения).

Установить `down_revision` = текущий alembic head.

### 3. Правка модели и сервиса
**Исполнитель:** `/executor-pro`
**Ревью:** `/lms-fastapi-techlead-code-reviewer`

- `app/models/tasks.py`:
  - Добавить поле:
    ```python
    order_position: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Позиция в курсе (NULL = автоматически в конец)",
    )
    ```
  - Обновить docstring класса: «Задания курсов. Порядок показа (`order_position`) управляется триггерами БД.»

- `app/services/tasks_service.py`, метод `get_by_course` (строки 378-410) — переписать на явный `select`:
  ```python
  from sqlalchemy import and_, func, select
  stmt = (
      select(Tasks)
      .where(*filters)
      .order_by(Tasks.order_position.asc().nulls_last(), Tasks.id.asc())
      .limit(limit).offset(offset)
  )
  count_stmt = select(func.count()).select_from(Tasks).where(*filters)
  items = (await db.execute(stmt)).scalars().all()
  total = (await db.execute(count_stmt)).scalar() or 0
  return list(items), int(total)
  ```
  Сохранить сигнатуру и Returns кортежа `(items, total)` — никаких изменений API.

- `app/services/learning_engine_service.py`, строки 418-421:
  ```python
  tasks_stmt = (
      select(Tasks.id)
      .where(Tasks.course_id == course_id)
      .order_by(Tasks.order_position.asc().nulls_last(), Tasks.id.asc())
  )
  ```

- `app/repos/tasks_repo.py` — без изменений.

### 4. Контракт (обязательно в этом же PR)
**Исполнитель:** `/executor-lite`

Обновить `docs/database-triggers-contract.md`:
- **Раздел 13** — копия раздела 7 (materials) с заменой `materials`→`tasks`, `set_material_order_position`→`set_task_order_position`, `app.skip_material_order_trigger`→`app.skip_task_order_trigger`.
- **Раздел 14** — копия раздела 8 (delete reorder) с теми же заменами.
- В таблицу триггеров в конце документа добавить 2 строки: `trg_set_task_order_position` и `trg_reorder_tasks_after_delete`.
- Под разделом 13 добавить **«⚠️ ОТЛИЧИЕ ОТ MATERIALS»**:
  > `tasks.external_uid` имеет глобальный `UNIQUE` (не `UNIQUE(course_id, external_uid)` как у materials). Это не влияет на работу триггеров `order_position` (партиция всё равно по `course_id`), но при bulk-импорте по `external_uid` следует учитывать кросс-курсовую уникальность.
- Под разделом 14 добавить **«Рекомендация для bulk-импорта»**:
  > При массовых INSERT'ах с явным возрастающим `order_position` каждый ряд триггерит UPDATE сдвига всех `>=NEW.order_position` — это O(N²) внутри транзакции. Для bulk-импорта рекомендуется передавать `order_position=NULL` — триггер расставит позиции по порядку INSERT'ов (MAX+1).
- Обновить «История изменений» в конце документа.

### 5. Cross-project mirror
**Исполнитель:** `/executor-lite`

В `D:\Work\ContentBackbone\docs\cross-project\`:
- `contracts/lms-db-schema.md`: добавить в раздел tasks колонку `order_position INTEGER NULL`, два триггера (по аналогии с materials), индекс `idx_tasks_course_order`. Указать alembic revision и дату.
- `CHANGELOG.md`: append в начало запись `Project: LMS / Change: tasks.order_position + triggers / Impact: API order, bulk-upsert / Action: TG_LMS и SPW могут сортировать по order_position / Authority: docs/database-triggers-contract.md разделы 13-14 / Refs: PR-link`.
- `STATE.md`: если меняется minor-version — bump.

Затем отдельный commit в ContentBackbone: `git -C D:\Work\ContentBackbone add docs/cross-project && git commit -m "cross-project: LMS tasks.order_position + triggers"`.

### 6. Tests
**Исполнитель:** `/fastapi-api-developer`

Создать `tests/test_tasks_order_position.py` — integration-тесты с реальной БД (Learn). Кейсы T1-T15, T25 из тест-плана (см. authority_testplan).

Обязательные сценарии:
- T1-T9: триггер INSERT/UPDATE (NULL, явный, сдвиг вверх/вниз, переключение NULL↔value, изоляция по course_id).
- T10-T13: триггер DELETE single, multi-row (regression statement-level), delete last, multi-course DELETE.
- T14-T15: backfill идемпотентность (migrate up→down→up даёт одинаковый order_position для всех существующих tasks).
- T25: LE-snapshot — fixture с N tasks в нескольких курсах, baseline `ORDER BY id`, миграция/бекфилл, `ORDER BY order_position NULLS LAST, id`. Списки id должны совпадать.

Дополнительно — **F5 negative test** (критический пробел из ревью):
```python
async def test_skip_task_order_trigger_session_var_is_transaction_local(db):
    """После commit транзакции с set_config app.skip_task_order_trigger='true'
    следующая транзакция не должна видеть значение."""
    async with db.begin():
        await db.execute(text("SELECT set_config('app.skip_task_order_trigger','true',true)"))
        # ... INSERT который БЫ обошёл триггер
    # новая транзакция:
    async with db.begin():
        val = (await db.execute(text("SELECT current_setting('app.skip_task_order_trigger', true)"))).scalar()
        assert val in (None, '', 'false'), f"session var утекла между транзакциями: {val!r}"
```

### 7. Review-changes артефакты
**Исполнитель:** `/fastapi-api-developer`
- `reviews/2026-05-21-tasks-order-position-stage1.md` — заголовок, контекст, начало diff, ссылки на бриф/тест-план/контракт.
- `reviews/2026-05-21-tasks-order-position-stage1.diff` — `git diff` после коммитов.

## Контракт навигации
N/A (фаза без UI и без новых endpoints).

## Запрещённые элементы управления
N/A.

## Frontend Routes
N/A — изменения только в backend.

## API Endpoints
- **Контракт без изменений в этой фазе.** `GET /api/v1/tasks/by-course/{course_id}` сохраняет сигнатуру и payload. Меняется только **детерминированность порядка**: до — недетерминированный; после — `ORDER BY order_position NULLS LAST, id`. Это уточнение поведения, не breaking change.
- `POST/PATCH /api/v1/tasks` и `POST /api/v1/tasks/bulk-upsert` — без изменений в этой фазе (поле появится в ТЗ-2).

## Concurrency & Idempotency
- **Миграция:** один раз `alembic upgrade head`. Downgrade удаляет всё, перезапуск upgrade идемпотентен по структуре (но не по данным — `order_position` будет пересчитан, что эквивалентно по value).
- **Backfill UPDATE:** идемпотентен — повторный запуск даёт те же `order_position` (детерминированная формула по id).
- **Триггер BEFORE INSERT/UPDATE:** использует `app.skip_task_order_trigger` с `is_local=true` — значение видно только в текущей транзакции, по COMMIT/ROLLBACK сбрасывается.
- **Триггер AFTER DELETE:** statement-level + REFERENCING OLD TABLE — нет TriggeredDataChangeViolationError при multi-row DELETE.
- **Race INSERT NULL × NULL:** известный issue (унаследован от materials, см. R1 в брифе) — НЕ закрываем в этой фазе.

## SQL formula verification

Backfill formula `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)`:
- Корректно для любого распределения id: монотонно растёт внутри партиции course_id.
- Совпадает с прежним поведением LE (`ORDER BY id ASC`) → инвариант «порядок не меняется» сохранён.
- Edge: пустая партиция → no-op.

DELETE reorder formula (statement-level, copy from materials fix):
```sql
ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY order_position NULLS LAST, id)
```
- Mental trace: course_id=10 имел tasks (id, order_position) = [(100,1),(101,2),(105,3)]. DELETE id=101. После: [(100, new=1), (105, new=2)] — NULL отсутствуют, корректный пересчёт.

## Stage Dependency Graph

| Stage | Statuses | BLOCKED_BY |
|---|---|---|
| **ТЗ-1 / Этап 1: Preflight (/db-check)** | new | — |
| **ТЗ-1 / Этап 2: Миграция** | new | Этап 1 (baseline snapshot) |
| **ТЗ-1 / Этап 3: Model+Service+LE** | new | Этап 2 (колонка существует) |
| **ТЗ-1 / Этап 4: Контракт** | new | Этап 2 (структура зафиксирована) |
| **ТЗ-1 / Этап 5: CB mirror** | new | Этап 2 + Этап 4 |
| **ТЗ-1 / Этап 6: Tests** | new | Этап 3 |
| **ТЗ-1 / Этап 7: Review artefacts** | new | Этап 6 (тесты PASS) |
| **ТЗ-2** | scheduled | **BLOCKED_BY: ТЗ-1 целиком (колонка должна существовать)** |

## Критерии приёмки

1. `alembic upgrade head` проходит на чистой реплике Learn без ошибок.
2. После upgrade: `SELECT COUNT(*) FROM tasks WHERE order_position IS NULL` = 0.
3. После upgrade: `SELECT course_id, MIN(order_position), MAX(order_position), COUNT(*) FROM tasks GROUP BY course_id` — `MIN=1` и `MAX=COUNT` для каждого course_id.
4. После upgrade snapshot test PASS: `ORDER BY id` (baseline до миграции) ≡ `ORDER BY order_position NULLS LAST, id` (после).
5. Integration-тесты `test_tasks_order_position.py` PASS (минимум T1-T15, T25, F5).
6. `alembic downgrade -1` → `alembic upgrade head` идемпотентен (T15).
7. Существующий suite `pytest tests/` без новых регрессий.
8. Smoke DELETE multi-row проходит без `TriggeredDataChangeViolationError`.
9. CB mirror: `git -C D:\Work\ContentBackbone log --oneline -1 -- docs/cross-project` показывает свежий commit с описанием LMS-изменений.
10. `docs/database-triggers-contract.md` содержит разделы 13-14 + 2 новых строки в таблице триггеров.

## Команды проверки

```powershell
# 0. Baseline snapshot (до миграции)
cd D:\Work\LMS
.\venv\Scripts\activate
$env:PYTHONIOENCODING="utf-8"
python -c "import asyncio; from app.db.base import engine; from sqlalchemy import text; ..."  # либо MCP query

# 1. Применить миграцию
alembic upgrade head

# 2. MCP проверка структуры
# (через postgresql MCP алиас):
#   SELECT column_name, data_type, is_nullable FROM information_schema.columns
#   WHERE table_name='tasks' AND column_name='order_position';
#   SELECT trigger_name FROM information_schema.triggers
#   WHERE event_object_table='tasks' AND trigger_name LIKE '%order%';
#   SELECT indexname FROM pg_indexes WHERE tablename='tasks' AND indexname='idx_tasks_course_order';

# 3. MCP проверка данных
#   SELECT course_id, COUNT(*) FILTER (WHERE order_position IS NULL) AS null_pos,
#          MIN(order_position), MAX(order_position), COUNT(*)
#   FROM tasks GROUP BY course_id ORDER BY course_id;
# ожидание: null_pos=0, MAX=COUNT, MIN=1

# 4. Snapshot equivalence test (Python)
pytest tests/test_tasks_order_position.py -k snapshot -v

# 5. Триггер smoke
pytest tests/test_tasks_order_position.py -v

# 6. Full regression
pytest tests/ -x

# 7. Smoke API (поведение get_by_course детерминировано)
curl -s http://localhost:8000/api/v1/tasks/by-course/1 | python -m json.tool

# 8. Roll-forward/back/forward
alembic downgrade -1
alembic upgrade head
pytest tests/test_tasks_order_position.py -k idempotent -v
```

## Артефакты review-gate

- `reviews/2026-05-21-tasks-order-position-stage1.md` — review-артефакт с контекстом и diff-хедером.
- `reviews/2026-05-21-tasks-order-position-stage1.diff` — `git diff main..feature/tsk-004-tasks-order-position-stage1`.
- `reviews/2026-05-21-tasks-order-position-baseline.csv` — baseline `(id, course_id)` до миграции.
- `reviews/2026-05-21-tasks-order-position-after.csv` — снимок `(id, course_id, order_position)` после миграции.
- Краткий smoke-log: вывод alembic upgrade, MCP-запросов 2-3, pytest summary.

## Переиспользование общей инфраструктуры

| Что переиспользуем | Откуда | Как |
|---|---|---|
| Тело PL/pgSQL `set_task_order_position` | `set_material_order_position` в migrations/20260129_100000 | Копия с заменой имён таблицы и session-var |
| Тело PL/pgSQL `reorder_tasks_after_delete` | finальная (statement-level) версия из migrations/20260205_140000 | Копия с теми же заменами |
| Структура разделов 13-14 контракта | разделы 7-8 | Копия + diff блок «отличия от materials» |
| Шаблон integration-тестов | tests/test_triggers_smoke.py | Параллельный модуль для tasks |
| Pattern LE `ORDER BY order_position NULLS LAST, id` | learning_engine_service.py:383 (materials) | Копия в строки 418-421 (tasks) |

Generic-обёртка PL/pgSQL (один параметризованный set_order_position(table, partition_col)) **отклонена** в брифе как переинженерство.

## Preflight / Deployment Checklist

- [ ] `alembic current` совпадает с `alembic heads` (один head, нет дивергентных веток).
- [ ] Зафиксирован baseline `(id, course_id)` всех tasks в CSV.
- [ ] Бэкап БД Learn (operator-step, до миграции в проде).
- [ ] Проверка отсутствия orphan tasks (course_id с несуществующим courses.id).
- [ ] PG-версия ≥ 13 (нужно `REFERENCING OLD TABLE`).
- [ ] `pytest tests/` без падений на ветке до миграции (baseline).
- [ ] `D:\Work\ContentBackbone` git status clean — для отдельного commit'а cross-project.
- [ ] Доступность locally: psql, MCP postgresql alias.

## Live smoke test (после deploy)

```powershell
# Optional gated через env-var TASKS_ORDER_LIVE_SMOKE=1
$env:TASKS_ORDER_LIVE_SMOKE="1"
pytest tests/test_tasks_order_position.py::test_live_smoke_create_delete_chain -v
```
Содержание `test_live_smoke_create_delete_chain` (включён только при `TASKS_ORDER_LIVE_SMOKE=1`):
1. POST /tasks с `order_position=NULL` в курс X → ответ 201, MCP-проверка `order_position == MAX_old + 1`.
2. POST /tasks с `order_position=1` в курс X → существующие сдвинуты на +1.
3. DELETE задачи в середине → MCP-проверка пересчёта.
4. Cleanup создания.

## Артефакты передачи

- Бранч: `feature/tsk-004-tasks-order-position-stage1`.
- Commit-messages (RU, императив): `feat: добавить order_position в tasks с триггерами PL/pgSQL`, `feat: переключить Learning Engine и get_by_course на order_position NULLS LAST, id`, `docs: разделы 13-14 контракта триггеров для tasks`, `test: integration tests для триггеров tasks.order_position`, `cross-project: LMS tasks.order_position + triggers` (отдельный commit в CB).
- PR description должен ссылаться на бриф, тест-план и authority контракт.
- После merge — перевести этап 1.6 в `D:\Work\Root\tasks\tsk-004-poryadok-v-lms.md` (создать запись «История движения») и разблокировать ТЗ-2.

## Риски и откат

| Риск | Уровень | Митигация |
|---|---|---|
| Бекфилл создаёт неверный порядок | Низкий | Snapshot-тест T25 + MCP-проверка `MAX=COUNT, MIN=1` после миграции |
| TriggeredDataChangeViolationError при DELETE | Низкий | С первой миграции statement-level + REFERENCING OLD TABLE (урок materials). Покрыто T11 |
| Race INSERT NULL × NULL → дубликат позиции | Низкий | Известный issue (унаследован), TODO `D:\Work\Root\tasks\` |
| `app.skip_task_order_trigger` утекает между транзакциями | Низкий | `is_local=true` в `set_config`, F5-тест в test-suite |
| Регрессия LE — студент видит другой next-item | Низкий | Snapshot-тест T25 + бекфилл по id ASC сохраняет порядок |
| Откат миграции теряет позиции | Принимается | downgrade удаляет колонку. После повторного upgrade — бекфилл по id ASC восстанавливает |
| Cross-project mirror забыт | Средний | Отдельный пункт в acceptance criteria #9 |

**Откат:**
```powershell
alembic downgrade -1
# восстановит структуру до миграции; данные order_position теряются (downgrade ожидаем)
git revert <commit-hash>  # для LMS-кода
git -C D:\Work\ContentBackbone revert <cb-commit>  # для mirror
```
