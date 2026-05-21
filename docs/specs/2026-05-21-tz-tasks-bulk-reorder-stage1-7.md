---
id: tz-tasks-bulk-reorder-stage1-7
parent_task: tsk-004 (этап 1.7)
created: 2026-05-21
status: ready-for-implementation
authority_brief: docs/briefs/tsk-004-tasks-bulk-reorder.md
authority_review: D:\Work\TG_LMS\docs\briefs\tsk-NNN-methodist-tasks-ordering.md
template_source: app/api/v1/materials_extra.py:157-174
blocks: tsk-NNN-methodist-tasks-ordering (TG_LMS)
review_skill: tech-spec-composer
---

# ТЗ: POST /api/v1/courses/{course_id}/tasks/reorder

## Цель

Добавить в LMS API endpoint массового изменения порядка заданий курса
`POST /api/v1/courses/{course_id}/tasks/reorder`, зеркально existing
`POST /api/v1/courses/{course_id}/materials/reorder`. Endpoint принимает в теле
список `(task_id, order_position)`, выполняет атомарный bulk-UPDATE в одной
транзакции с временно выключенным триггером `trg_set_task_order_position`,
возвращает обновлённые позиции.

Пользовательский результат: будущий веб-фронт методиста сможет реализовать
drag-and-drop переупорядочивание заданий курса одним атомарным запросом,
без промежуточных неконсистентных состояний.

## Контекст

**Репозиторий:** `D:\Work\LMS` (Python 3.10+, FastAPI, SQLAlchemy 2.x async,
PostgreSQL, Alembic).

**Текущее поведение:**
- Поле `tasks.order_position` существует и заполнено для 567 строк (бекфилл
  `ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY id ASC)`), миграция
  `20260521_120000_tasks_order_position_triggers.py`.
- Триггеры BEFORE INSERT/UPDATE `trg_set_task_order_position` и AFTER DELETE
  `trg_reorder_tasks_after_delete` управляют order_position автоматически.
- Session-variable `app.skip_task_order_trigger='true'` (local transaction)
  отключает триггер для bulk-операций — реализовано в миграции, не использовано
  ни в одном сервисе LMS на данный момент.
- `PATCH /api/v1/tasks/{task_id}` принимает `order_position` (фаза 2,
  commit `63bcddd`) — атомарная single-task операция.
- Index `idx_tasks_course_order ON tasks (course_id, order_position NULLS LAST)`.

**Зеркальный шаблон:** [`app/api/v1/materials_extra.py:157-174`](../../app/api/v1/materials_extra.py#L157-L174)
(router), [`app/services/materials_service.py:80-110`](../../app/services/materials_service.py#L80-L110) (service),
[`app/repos/materials_repo.py:108-138`](../../app/repos/materials_repo.py#L108-L138) (repo),
[`app/schemas/materials.py:91-150`](../../app/schemas/materials.py#L91-L150) (схемы).

**Внешние зависимости:** нет новых. Используются existing httpx-free LMS layers.

## Границы задачи

### Входит

1. 4 новых Pydantic-класса в `app/schemas/tasks.py`.
2. 1 новый endpoint в `app/api/v1/tasks_extra.py`.
3. 1 новый метод `reorder_tasks` в `app/services/tasks_service.py` с **расширенной валидацией** относительно materials (см. D3).
4. 1 новый метод `reorder_tasks` в `app/repos/tasks_repo.py` (зеркало materials_repo).
5. Новый тестовый файл `tests/test_tasks_reorder_api.py` (BR1-BR8).
6. Раздел 15 в `docs/database-triggers-contract.md` о session-variable `app.skip_task_order_trigger` и bulk-операциях.

### НЕ входит (out of scope)

| Что | Куда отнесено |
|-----|---------------|
| Single-move endpoint `POST /api/v1/tasks/{task_id}/move` | отказались (eng-review) |
| Изменение триггеров PL/pgSQL | не требуется |
| Изменение `TaskCreate/Update/Read` | уже сделано в фазе 2 |
| TG_LMS-сторона (`api_client.reorder_course_tasks`, UI методиста) | отдельная задача `tsk-NNN` в TG_LMS |
| OpenAPI snapshot regen | автоматический в CI/CD |
| Cross-project mirror в ContentBackbone | post-merge step (отдельный commit в CB) |
| Auth на endpoint-level | используется middleware X-API-Key (как у materials) |
| Pagination/streaming больших reorder | не нужно, типичный курс ≤50 заданий |

### Не трогать

- Существующие endpoints в `tasks_extra.py` и `tasks.py`
- Existing методы `materials_service.reorder_materials` и `materials_repo.reorder_materials` (этап B в TG_LMS, не здесь)
- Триггеры PL/pgSQL `set_task_order_position`, `reorder_tasks_after_delete`
- Бэкфилл-логику `order_position` для существующих 567 строк

## Стек и ограничения

- Python 3.10+, FastAPI, SQLAlchemy 2.x async, PostgreSQL.
- Async/await везде; никаких sync-вызовов в новом коде.
- Соблюдение архитектурного слоя LMS: Router → Service (валидация + бизнес) → Repo (SQL).
- Type hints обязательны; docstrings RU.
- Никаких новых внешних зависимостей.
- Тесты — `pytest-asyncio`, парадигма `flush()` без `commit()` (как `test_tasks_order_position.py`)
  — **внимание:** для repo используется `db.commit()` внутри метода (атомарность транзакции),
  тесты должны учитывать это и использовать `db.rollback()` после или отдельную тестовую базу.

## Обязательные скиллы/правила

- DRY — общая логика в repo/service, тонкие router-handlers.
- Не глотать исключения — пробрасывать `DomainError` со статус-кодом.
- `logging` обязателен — каждый успешный reorder + каждая валидационная ошибка должны логироваться (см. шаблон materials_extra.py:170).
- Никаких эмодзи в логах.
- Все строки запросов — параметризованы, никакого SQL-injection.

## Шаги реализации

### Шаг 1. Pydantic-схемы

**Файл:** `app/schemas/tasks.py` (добавить блок после existing `TaskRead`).

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/techlead-code-reviewer` (контракт API)

Добавить 4 класса (зеркало `app/schemas/materials.py:91-150` с заменой `material_id → task_id`):

```python
class TaskOrderItem(BaseModel):
    """Элемент списка порядка заданий при reorder."""
    task_id: int = Field(..., description="ID задания")
    order_position: int = Field(..., ge=1, description="Новая позиция в курсе")


class TaskReorderRequest(BaseModel):
    """Запрос на изменение порядка заданий курса."""
    task_orders: List[TaskOrderItem] = Field(
        ...,
        description="Список пар (task_id, order_position) для установки нового порядка",
    )


class TaskOrderRead(BaseModel):
    """Элемент ответа reorder: id задания и его новая позиция."""
    id: int
    order_position: int


class TaskReorderResponse(BaseModel):
    """Ответ на изменение порядка заданий."""
    updated: int = Field(..., ge=0)
    tasks: List[TaskOrderRead] = Field(default_factory=list)
```

**Валидация на уровне схемы (Pydantic):**
- `order_position >= 1` обеспечивается `Field(ge=1)` — автоматически 422 от FastAPI на отрицательных/нулевых значениях.
- Дубликаты task_id и order_position **не валидируются на уровне схемы** — проверка вынесена в service (см. Шаг 3), чтобы можно было вернуть осмысленное сообщение об ошибке с конкретным ID.

### Шаг 2. Repository слой

**Файл:** `app/repos/tasks_repo.py` (добавить метод после existing CRUD).

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/db-check` (взаимодействие с session-variable и триггером), `/techlead-code-reviewer`

Метод **зеркальный** [`app/repos/materials_repo.py:108-138`](../../app/repos/materials_repo.py#L108-L138):

```python
async def reorder_tasks(
    self,
    db: AsyncSession,
    course_id: int,
    task_orders: List[Dict[str, int]],
) -> List[Tasks]:
    """
    Массовое изменение порядка заданий курса.
    Отключает триггер trg_set_task_order_position на время операции
    (через session-variable app.skip_task_order_trigger), затем выполняет
    UPDATE order_position для каждой пары (task_id, order_position).
    Коммитит транзакцию в конце. Возвращает обновлённые задания, отсортированные
    по order_position NULLS LAST.
    """
    if not task_orders:
        return []

    await db.execute(text("SELECT set_config('app.skip_task_order_trigger', 'true', true)"))
    for item in task_orders:
        tid = item["task_id"]
        pos = item["order_position"]
        await db.execute(
            update(Tasks)
            .where(Tasks.id == tid, Tasks.course_id == course_id)
            .values(order_position=pos)
        )
    await db.commit()

    task_ids = [item["task_id"] for item in task_orders]
    stmt = select(Tasks).where(
        Tasks.course_id == course_id,
        Tasks.id.in_(task_ids),
    ).order_by(Tasks.order_position.asc().nulls_last())
    result = await db.execute(stmt)
    return list(result.scalars().all())
```

**Заметка по session-variable** (поведение установлено миграцией `20260521_120000`):
`set_config('app.skip_task_order_trigger', 'true', true)` — третий параметр `true`
означает `is_local` (действие в пределах текущей транзакции). После `db.commit()`
значение сбрасывается. Триггер `trg_set_task_order_position` имеет условие
`WHEN (current_setting('app.skip_task_order_trigger', true) IS DISTINCT FROM 'true')`,
то есть пропускается, пока флаг установлен.

### Шаг 3. Service слой (с расширенной валидацией)

**Файл:** `app/services/tasks_service.py` (добавить метод).

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/techlead-code-reviewer` (валидация, contracts, edge cases)

Метод **расширяет** materials-шаблон тремя проверками валидации:

```python
async def reorder_tasks(
    self,
    db: AsyncSession,
    course_id: int,
    task_orders: List[Dict[str, int]],
) -> List[Tasks]:
    """
    Массовое изменение порядка заданий курса.
    Валидация:
    - 404: курс course_id не найден
    - 422: дубликат task_id в теле
    - 422: дубликат order_position в теле
    - 400: task_id не принадлежит курсу course_id
    """
    if not task_orders:
        return []

    # 1. Курс существует
    course = await self._courses_repo.get(db, course_id)
    if not course:
        raise DomainError(f"Курс с ID {course_id} не найден", status_code=404)

    # 2. Нет дубликатов task_id
    task_ids = [item["task_id"] for item in task_orders]
    if len(task_ids) != len(set(task_ids)):
        duplicates = [tid for tid in set(task_ids) if task_ids.count(tid) > 1]
        raise DomainError(
            f"Обнаружены дубликаты task_id в теле запроса: {duplicates}",
            status_code=422,
        )

    # 3. Нет дубликатов order_position
    positions = [item["order_position"] for item in task_orders]
    if len(positions) != len(set(positions)):
        duplicates = [p for p in set(positions) if positions.count(p) > 1]
        raise DomainError(
            f"Обнаружены дубликаты order_position в теле запроса: {duplicates}",
            status_code=422,
        )

    # 4. Все task_id принадлежат курсу
    items = await self.repo.list_by_course(db, course_id, limit=10000)
    # NOTE: для tasks list_by_course возвращает List[Tasks] (не tuple), уточнить сигнатуру в реализации
    ids_in_course = {t.id for t in items}
    for tid in task_ids:
        if tid not in ids_in_course:
            raise DomainError(
                f"Задание с ID {tid} не принадлежит курсу {course_id} или не найдено",
                status_code=400,
            )

    # 5. Bulk UPDATE через repo
    try:
        return await self.repo.reorder_tasks(db, course_id, task_orders)
    except IntegrityError as e:
        raise DomainError(
            f"Ошибка при изменении порядка заданий: {e!s}",
            status_code=400,
        )
```

**Заметка по поведению:**
- **Partial reorder допустим** (D4): можно прислать порядок только для подмножества заданий курса.
  Остальные задания сохраняют свои текущие `order_position` (не сдвигаются).
- **Не валидируется:** заполнен ли весь диапазон позиций без gap'ов (1, 2, 3, ...).
  Drag-list UI присылает свои позиции; gap'ы допустимы.
- **Не валидируется:** позиция уникальна относительно остальных заданий курса
  (тех, что не в теле). После reorder возможны коллизии типа «в теле task_id=5 идёт на
  pos=3, но в курсе уже есть task_id=7 на pos=3, который не в теле» — БД примет
  обе строки (триггер выключен). Это **сознательное** поведение партиального
  reorder; клиент отвечает за консистентность.

### Шаг 4. Router слой

**Файл:** `app/api/v1/tasks_extra.py` (добавить endpoint после existing POST-операций).

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/techlead-code-reviewer` (HTTP-контракт, status codes)

```python
@router.post(
    "/courses/{course_id}/tasks/reorder",
    response_model=TaskReorderResponse,
    summary="Изменить порядок заданий курса",
)
async def reorder_course_tasks(
    course_id: int,
    body: TaskReorderRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> TaskReorderResponse:
    """
    Массовое изменение порядка заданий курса.
    Принимает список (task_id, order_position); устанавливает позиции атомарно
    в одной транзакции с временно отключённым триггером trg_set_task_order_position.
    Возвращает обновлённые задания, отсортированные по order_position.

    Используется веб-фронтом методиста для drag-and-drop переупорядочивания.
    Telegram-бот использует single-PATCH /api/v1/tasks/{task_id} с триггером.
    """
    task_orders = [
        {"task_id": x.task_id, "order_position": x.order_position}
        for x in body.task_orders
    ]
    tasks = await tasks_service.reorder_tasks(db, course_id, task_orders)
    logger.info(
        "reorder_tasks course_id=%s updated=%s",
        course_id, len(tasks),
    )
    return TaskReorderResponse(
        updated=len(tasks),
        tasks=[
            TaskOrderRead(id=t.id, order_position=t.order_position or 0)
            for t in tasks
        ],
    )
```

**Импорты в `tasks_extra.py`:**
- Из `app/schemas/tasks.py` добавить: `TaskReorderRequest`, `TaskReorderResponse`, `TaskOrderRead`.
- Сервис `tasks_service` — already imported.

### Шаг 5. Тесты

**Файл:** `tests/test_tasks_reorder_api.py` (новый).

**Исполнитель:** `/fastapi-api-developer`
**Ревью:** `/db-check` (атомарность, session-variable), `/techlead-code-reviewer` (полнота)

Парадигма — async `pytest-asyncio`, использовать существующие fixtures из `conftest.py`
и helpers из `test_tasks_order_position.py` (`_insert_task`, `_course_positions`).

| ID | Сценарий | Тип проверки |
|----|----------|-------------|
| **BR1** | Полный reorder 5 заданий в новом порядке | 200; `updated=5`; `_course_positions(course_id)` соответствует новому порядку |
| **BR2** | Атомарность: 1 task_id не принадлежит курсу | 400; `_course_positions` идентичен состоянию ДО запроса (rollback) |
| **BR3** | Дубликат `task_id` в теле | 422; `_course_positions` идентичен состоянию ДО |
| **BR4** | Дубликат `order_position` в теле | 422; `_course_positions` идентичен состоянию ДО |
| **BR5** | Конкурентный PATCH (другая сессия) + reorder одновременно | оба завершаются без deadlock; final state валиден |
| **BR6** | После reorder триггер снова активен | INSERT нового задания без order_position → триггер ставит MAX+1 корректно |
| **BR7** | Partial reorder (3 из 5 заданий курса) | 200; перечисленные 3 получили новые позиции; остальные 2 сохранили старые |
| **BR8** | LE snapshot equivalence: студент с уже выданным snapshot после reorder видит **старый** порядок | (depends on LE snapshot infra) — повторно проверить инвариант из T25 |
| **BR9** | Пустой `task_orders: []` | 200; `updated=0`; `tasks=[]` |
| **BR10** | `course_id` не существует | 404 |
| **BR11** | Отрицательная `order_position` в теле | 422 (от Pydantic Field ge=1) |

**Concurrency & Idempotency (отдельная sub-section):**
- **BR5** — гонка PATCH+reorder проверяет, что транзакция reorder не блокирует
  чужие single-task PATCH'и на других строках того же курса, и наоборот.
- **Идемпотентность:** повторная отправка идентичного reorder-запроса даёт идентичный
  result (200, те же позиции). Это покрыто BR1 + повтор (BR1.repeat).

### Шаг 6. Документация контракта триггеров

**Файл:** `docs/database-triggers-contract.md` (добавить раздел).

**Исполнитель:** `/fastapi-api-developer` (документация)

Раздел 15: «Tasks bulk reorder & session-variable `app.skip_task_order_trigger`»:
- Описание паттерна `set_config(..., 'true', true)` → bulk UPDATE → `commit` → флаг сброшен.
- Каким endpoint'ом используется (`POST /api/v1/courses/{course_id}/tasks/reorder`).
- Гарантия атомарности через единственный commit.
- Зеркало раздела 7-8 для materials.

### Шаг 7. Cross-project mirror (post-merge)

**Исполнитель:** `/fastapi-api-developer` (post-merge — ручное обновление CB-mirror)
**Ревью:** `/review-gate` (PASS перед commit в ContentBackbone)

**Не в этой ветке LMS** — отдельный commit в `D:\Work\ContentBackbone`:

1. `docs/cross-project/contracts/lms-api.md` — добавить раздел:
   ```
   ### POST /api/v1/courses/{course_id}/tasks/reorder
   Atomic bulk reorder of tasks in a course. Body: {task_orders: [{task_id, order_position}]}.
   Response: {updated: int, tasks: [{id, order_position}]}.
   Validation: 404 course not found, 422 duplicates, 400 task not in course.
   Trigger interaction: temporarily disabled via session-variable app.skip_task_order_trigger.
   ```
2. `docs/cross-project/CHANGELOG.md` — добавить запись `tsk-004 этап 1.7`.
3. `docs/cross-project/STATE.md` — без изменений (фаза LMS не меняется).

## Контракт навигации

Не применимо — backend-only ТЗ, нет UI.

## Запрещённые элементы управления

Не применимо.

## Критерии приёмки

1. **Создан endpoint** `POST /api/v1/courses/{course_id}/tasks/reorder` с response_model `TaskReorderResponse`.
2. **Все 4 Pydantic-класса** добавлены в `app/schemas/tasks.py`, экспортированы.
3. **Service-валидация** покрывает 4 случая ошибок (404 курс, 422 дубль task_id, 422 дубль position, 400 task не в курсе) с понятными сообщениями на русском.
4. **Repo-метод** атомарен — все UPDATE'ы и commit в одной транзакции; `set_config('app.skip_task_order_trigger', 'true', true)` устанавливается до UPDATE'ов.
5. **Тесты BR1-BR11 проходят** локально (`pytest tests/test_tasks_reorder_api.py -v`).
6. **Все existing тесты задач не падают** (`pytest tests/test_tasks_order_position*.py -v`) — регрессия отсутствует.
7. **Полный suite тестов проходит** (`pytest -q`).
8. **Логирование** — успешный reorder и каждая валидационная ошибка логируются с `course_id` и `len(task_orders)`.
9. **docs/database-triggers-contract.md** содержит раздел 15.
10. **OpenAPI-snapshot** — после merge в `main` CI/CD регенерирует `docs/openapi.json` с новым endpoint автоматически (acceptance не блокирует на этом шаге).
11. **Cross-project mirror** — отдельный commit в ContentBackbone с обновлёнными `contracts/lms-api.md` и `CHANGELOG.md` (post-merge).

## Команды проверки

```powershell
# Smoke: запустить новые тесты
cd D:\Work\LMS
pytest tests/test_tasks_reorder_api.py -v

# Регрессия: existing tasks-тесты
pytest tests/test_tasks_order_position.py tests/test_tasks_order_position_api.py -v

# Полный suite
pytest -q

# Проверка endpoint через uvicorn (manual)
uvicorn app.main:app --reload --port 8000
# В другом окне:
curl -X POST http://localhost:8000/api/v1/courses/1/tasks/reorder ^
     -H "Content-Type: application/json" ^
     -H "X-API-Key: <dev-key>" ^
     -d "{\"task_orders\":[{\"task_id\":10,\"order_position\":1},{\"task_id\":11,\"order_position\":2}]}"

# Просмотр свежей версии OpenAPI (после старта uvicorn)
curl http://localhost:8000/openapi.json | grep -A 3 "tasks/reorder"
```

## Артефакты review-gate

1. **Diff** ветки относительно `main` (4 файла кода + 1 файл тестов + 1 файл документации).
2. **Pytest output** новых тестов `tests/test_tasks_reorder_api.py` — все passed.
3. **Pytest output** regression suite (`test_tasks_order_position*.py`) — все passed.
4. **Log evidence** успешного reorder в smoke-сессии (`logs/app.log` — строка `reorder_tasks course_id=... updated=...`).
5. **MCP-проверка через `learn_public_db`** (read-only):
   - `SELECT id, order_position FROM tasks WHERE course_id = <test_course> ORDER BY order_position` — соответствует ожидаемому порядку.
   - `SELECT current_setting('app.skip_task_order_trigger', true)` — должен быть `NULL` или `'false'` после завершения reorder (флаг сброшен по transaction-local правилу).
6. **Бриф** `docs/briefs/tsk-004-tasks-bulk-reorder.md` — обновлён status: `finalized`.

## Переиспользование общей инфраструктуры

| Слой | Что используется | Откуда |
|------|------------------|--------|
| Pydantic-паттерн | `Field(ge=1)`, `extra="forbid"` (если применяется в materials — не применяется), `List[X]` | `materials.py:91-150` |
| Router-паттерн | `@router.post(..., response_model=..., summary=...)`, `Depends(get_db)` | `materials_extra.py:157-174` |
| Service-паттерн | `DomainError(msg, status_code=NNN)` для ошибок; `IntegrityError → DomainError` для БД | `materials_service.py:80-110`, `app/core/errors.py` |
| Repo-паттерн | `set_config + bulk UPDATE + commit + SELECT` | `materials_repo.py:108-138` |
| Test fixtures | `conftest.py::async_db_session`, `_insert_task`, `_course_positions` | `tests/conftest.py`, `tests/test_tasks_order_position.py:42-76` |
| Trigger session-var | `app.skip_task_order_trigger` | миграция `20260521_120000_tasks_order_position_triggers.py` |

**Запрещено дублировать:**
- Логику валидации `task_id ∈ course_id` — переиспользовать `repo.list_by_course`.
- Логику работы с триггером — использовать существующий session-variable, не отключать триггер через `ALTER TABLE`.

## Артефакты передачи

После завершения этой ТЗ передать в следующий этап (`tsk-NNN` TG_LMS):

1. **OpenAPI-snapshot** `docs/openapi.json` с новым endpoint (после CI regen).
2. **Контракт ответа** (см. Pydantic `TaskReorderResponse`) — для api_client в TG_LMS.
3. **Bug-trail заметка** — если в ходе реализации обнаружены отличия от materials-шаблона (например, отсутствие `list_by_course` возвращающего tuple), документировать в footer ТЗ.
4. **Cross-project обновление** — после commit в LMS обновить `contracts/lms-api.md` в ContentBackbone (post-merge step).

## Риски и откат

| Риск | Вероятность | Митигация | Откат |
|------|-------------|-----------|-------|
| Триггер `trg_set_task_order_position` не пропускается, и наш UPDATE вызывает каскад | Низкая (паттерн проверен в materials) | BR1 + BR7 тесты обнаружат сразу | Revert PR; данные сохранены — endpoint не вышел в prod |
| `db.commit()` внутри repo несовместим с outer transaction tests | Средняя | Тесты используют `db.rollback()` после или фабрику с auto-commit | Изменить fixtures `conftest.py` (вне scope) или isolate тесты |
| Partial reorder вызывает дубликаты позиций в БД | Средняя (сознательное поведение) | Документировано в шаге 3 и в `docs/database-triggers-contract.md` раздел 15 | Не откатывать — клиент отвечает за консистентность; при необходимости — отдельная задача на «strict reorder» |
| LE snapshot для активных студентов изменился (T25 нарушен) | Низкая (bulk UPDATE = тот же UPDATE триггер пропускает) | BR8 проверяет инвариант | Revert PR |
| Конкурентный PATCH ловит deadlock | Низкая (PostgreSQL row-level locking) | BR5 проверяет | Уменьшить isolation level или добавить advisory lock — отдельная задача |
| Race-condition внутри одной session между set_config и UPDATE | Низкая (`is_local=true` гарантирует transaction-scoped) | Документировано; зеркало materials | Materials в проде не страдает — паттерн надёжный |

**Rollback procedure:**
1. Revert Git commit (single commit или squash-merge).
2. CI/CD сам регенерирует OpenAPI без endpoint'а.
3. Cross-project mirror — revert commit в ContentBackbone.
4. DB-side: нет миграций → нет rollback миграций.

## Concurrency & Idempotency

**Concurrency:**
- Один reorder использует single AsyncSession и одну транзакцию.
- Изоляция PostgreSQL по умолчанию (`READ COMMITTED`) — UPDATE'ы внутри транзакции
  не видят чужих параллельных UPDATE'ов до commit.
- Параллельный PATCH одной задачи: либо первый коммитится, второй ждёт row-lock,
  либо наоборот. Final state = последний UPDATE.
- Параллельный reorder того же курса: row-level locking; последовательное исполнение
  на конфликтных строках, параллельное — на разных.
- **Без advisory lock** — для типичного use case (1 методист, 1 курс) этого достаточно.

**Idempotency:**
- Endpoint **идемпотентен**: повторная отправка идентичного `TaskReorderRequest`
  даёт идентичный результат (тот же `TaskReorderResponse`). Нет side-effect
  beyond order_position UPDATE.
- **Не используется idempotency-key** — request body само по себе детерминирует
  результат (task_id → order_position mapping).

## Stage Dependency Graph

```
[Stage A] tsk-004 этап 1.6 (фазы 1+2) — order_position field + triggers + PATCH
    │
    │ COMPLETED (commits 1182d30, 63bcddd на 2026-05-21)
    ▼
[Stage A] tsk-004 этап 1.7 — bulk reorder endpoint  ←  ЭТО ТЗ
    │
    │ BLOCKED_BY: этап 1.6 фазы 1+2 (требуется order_position + триггеры)
    │
    ▼
[Stage B] tsk-NNN TG_LMS — api_client.reorder_course_tasks + UI методиста
    │
    │ BLOCKED_BY: tsk-004 этап 1.7 (требуется working endpoint для api_client smoke)
    │
    ▼
[Future] web-версия методиста с drag-list
    │
    │ BLOCKED_BY: tsk-004 этап 1.7 (требуется endpoint для drag-UX)
```

Каждое downstream stage явно ссылается на upstream через `BLOCKED_BY`.

## Preflight / Deployment Checklist

Не применимо — нет новых зависимостей, нет новых env-variables, нет новых внешних
сервисов. Endpoint использует existing LMS dev environment и existing `learn_public_db`.

Минимальная sanity-проверка перед smoke:
1. `psql -d Learn -c "SELECT * FROM pg_trigger WHERE tgname IN ('trg_set_task_order_position', 'trg_reorder_tasks_after_delete')"` — оба триггера existing.
2. `psql -d Learn -c "SELECT to_regprocedure('set_task_order_position'), to_regprocedure('reorder_tasks_after_delete')"` — обе функции existing.
3. `pytest tests/test_tasks_order_position.py -v` — baseline зелёный.

## Footer / Заметки реализации

- `repo.list_by_course` для tasks может возвращать другой shape (не tuple), чем materials. Уточнить и адаптировать шаг 3 при реализации.
- Если в реализации обнаружится, что `commit()` внутри repo конфликтует с фабрикой fixtures `conftest.py` — задокументировать в footer ТЗ и решить либо адаптацией fixtures (`async with db.begin_nested()`), либо вынесением commit на уровень router.
- В реализации проверить, что `Tasks` (модель) импортируется из `app.models.tasks`, а не из `app.db.models` (есть прецеденты обоих в проекте).
