---
id: tz-tasks-order-position-stage2
parent_task: tsk-004 (Этап 1.6, фаза 2)
created: 2026-05-21
status: blocked
blocked_by: tz-tasks-order-position-stage1
authority_brief: docs/briefs/tsk-004-tasks-order-position.md
authority_testplan: docs/briefs/tsk-004-tasks-order-position-testplan.md
authority_contract: docs/database-triggers-contract.md
---

# ТЗ-2: tasks.order_position — расширение API и bulk-upsert

## Цель
Выставить поле `order_position` на API-границе: Pydantic-схемы `TaskCreate / TaskUpdate / TaskRead / TaskUpsertItem` принимают и возвращают `order_position`; `TasksService.bulk_upsert` пробрасывает поле в обе ветки (CREATE / UPDATE). После ТЗ-2 внешний клиент (SPW, TG_LMS, Google Sheets импортёр) может явно управлять позицией задачи и видеть её в ответе.

## Контекст
- Репозиторий: `D:\Work\LMS`. Сборка после **ТЗ-1** (`feature/tsk-004-tasks-order-position-stage1`) уже залита в `main`.
- Затронутые модули: `app/schemas/tasks.py`, `app/services/tasks_service.py`, `app/api/v1/tasks_extra.py` (опционально — проверка регистраций), `docs/openapi.json` (regenerate), CB `lms-api.md`.
- На момент старта ТЗ-2: колонка `order_position` существует, триггеры активны, `get_by_course` уже сортирует по `order_position NULLS LAST, id`, LE — то же.
- Текущий `TaskUpsertItem` ([schemas/tasks.py:97-107](../../app/schemas/tasks.py#L97-L107)) — без `order_position`.
- Текущий `TasksService.bulk_upsert` ([services/tasks_service.py:200-271](../../app/services/tasks_service.py#L200-L271)) — хардкодит список полей в CREATE и UPDATE ветках.

## Границы задачи

### Входит
- `TaskCreate.order_position: Optional[int] = None`.
- `TaskUpdate.order_position: Optional[int] = None` — позволяет менять позицию через PATCH.
- `TaskRead.order_position: Optional[int] = None` — возвращается клиенту.
- `TaskUpsertItem.order_position: Optional[int] = None`.
- `TasksService.bulk_upsert`: проброс `order_position` в obj_in для CREATE и UPDATE ветвей. Условный: если поле отсутствует в payload UPDATE — позиция НЕ меняется (триггер сработает только при явном UPDATE значении).
- Smoke-тесты API: POST с явным order_position, POST без, PATCH с order_position, bulk-upsert с миксом NULL/значение.
- Regenerate `docs/openapi.json`.
- Cross-project mirror: обновить `D:\Work\ContentBackbone\docs\cross-project\contracts\lms-api.md` (Pydantic-схемы) + CHANGELOG.

### Не входит
- Изменение `/tasks/search` сортировки (решение 2C1).
- Изменение поведения триггеров.
- Fast-path в bulk-upsert (`DISABLE TRIGGER` для bulk-NULL-вставки) — follow-up при росте tasks.
- Frontend SPW UI для drag-and-drop переупорядочивания.
- TG_LMS UI для управления порядком заданий.

### Не трогать
- Миграции (фаза 1).
- LE сортировку (фаза 1).
- `/tasks/search`.
- `tasks_repo.py`.

## Стек и ограничения
Те же, что в ТЗ-1.
- Pydantic v2 (использует `model_config = ConfigDict(from_attributes=True)`).
- FastAPI router в `app/api/v1/tasks_extra.py` использует CRUD-роутер из `app/api/v1/routers/tasks.py` — поведение `POST /tasks`/`PATCH /tasks/{id}` определяется CRUD generic.

## Обязательные скиллы/правила
- `/fastapi-api-developer` — схемы, сервис, endpoints smoke.
- `/executor-pro` — bulk_upsert изменения (затрагивает импорт).
- `/executor-lite` — обновление `docs/openapi.json` и cross-project lms-api.md mirror.
- `/review-gate` — pre-merge.
- `/lms-fastapi-techlead-code-reviewer` — итоговое ревью (контракт API).
- `~/.claude/CLAUDE.md` + `d:\Work\LMS\CLAUDE.md`.

## Шаги реализации

### 1. Pydantic-схемы
**Исполнитель:** `/fastapi-api-developer`

В `app/schemas/tasks.py`:

```python
class TaskCreate(BaseModel):
    # ... существующие поля ...
    order_position: Optional[int] = Field(
        default=None,
        description="Позиция в курсе (NULL = в конец, триггер БД проставит MAX+1)",
    )


class TaskUpdate(BaseModel):
    # ... существующие поля ...
    order_position: Optional[int] = Field(
        default=None,
        description=(
            "Новая позиция в курсе. None = поле не передано (не менять). "
            "Чтобы перенести в конец, передать большой номер (MAX+1) или 0 для пересчёта"
            " — см. контракт триггера."
        ),
    )


class TaskRead(BaseModel):
    # ... существующие поля ...
    order_position: Optional[int] = None  # триггер всегда проставит; NULL остаётся только в race-edge


class TaskUpsertItem(BaseModel):
    # ... существующие поля ...
    order_position: int | None = None
```

**Внимание:** `TaskUpdate.order_position` имеет двойную семантику «None = не менять» / «N = менять на N». Это согласовано с существующим паттерном в `TaskUpdate` (где все поля Optional и None трактуется как «не передавать»). Документировать в docstring.

### 2. Сервис bulk_upsert
**Исполнитель:** `/executor-pro`
**Ревью:** `/lms-fastapi-techlead-code-reviewer` (contracts)

В `app/services/tasks_service.py` метод `bulk_upsert`:

- В CREATE-ветке добавить:
  ```python
  obj_in = {
      "external_uid": external_uid,
      "course_id": data["course_id"],
      "difficulty_id": data["difficulty_id"],
      "task_content": data["task_content"],
      "solution_rules": data.get("solution_rules"),
      "max_score": data.get("max_score"),
      "order_position": data.get("order_position"),  # NEW
  }
  ```
- В UPDATE-ветке добавить условный проброс — **только если ключ присутствует в payload и значение не None**:
  ```python
  obj_in = {
      "course_id": data["course_id"],
      "difficulty_id": data["difficulty_id"],
      "task_content": data["task_content"],
      "solution_rules": data.get("solution_rules"),
      "max_score": data.get("max_score"),
  }
  if data.get("order_position") is not None:
      obj_in["order_position"] = data["order_position"]
  ```
  Логика: если импорт не передаёт `order_position`, позиция существующей задачи не меняется. Если передаёт явное значение — триггер пересчитает порядок.

- В методах `create()` и `update()` тот же `order_position` идёт через `super().create()`/`super().update()` без специальной обработки — `BaseService` уже маппит dict → mapped column.

### 3. Регенерация openapi.json
**Исполнитель:** `/executor-lite`

```powershell
# при включённом dev-сервере или через offline-генератор:
python -c "from fastapi.openapi.utils import get_openapi; from app.main import app; import json; print(json.dumps(get_openapi(title=app.title, version=app.version, routes=app.routes), ensure_ascii=False, indent=2))" > docs\openapi.json
```

Проверить diff: должны появиться поля `order_position` в request bodies `TaskCreate`/`TaskUpdate`/`TaskUpsertItem` и в response `TaskRead`.

### 4. Cross-project mirror
**Исполнитель:** `/executor-lite`

В `D:\Work\ContentBackbone\docs\cross-project\`:
- `contracts/lms-api.md`: обновить блоки Pydantic-схем `TaskCreate / TaskUpdate / TaskRead / TaskUpsertItem` с новым полем.
- `CHANGELOG.md`: append запись `Project: LMS / Change: tasks API — order_position в Create/Update/Read/UpsertItem / Impact: SPW, TG_LMS, Google Sheets импортёр могут управлять позицией / Authority: docs/specs/2026-05-21-tz-tasks-order-position-stage2.md / Refs: PR-link`.
- Отдельный commit в ContentBackbone.

### 5. API smoke-тесты
**Исполнитель:** `/fastapi-api-developer`

В `tests/test_tasks_api_order_position.py`:
- T22-equivalent: `POST /tasks` с `order_position=K` создаёт задачу на позиции K, существующие сдвинуты.
- T17-equivalent: `POST /tasks` с `order_position=NULL/отсутствует` → MAX+1.
- T23-equivalent: `PATCH /tasks/{id}` с `order_position=M` меняет позицию, остальные пересчитаны.
- T21-equivalent: `GET /tasks/{id}` возвращает поле `order_position` в payload.
- T16-equivalent: `POST /tasks/bulk-upsert` с миксом `order_position=NULL` и явных значений → корректное распределение.
- T18-equivalent: `POST /tasks/bulk-upsert` updates существующей с новым `order_position` → пересчёт.
- T19-equivalent: `POST /tasks/bulk-upsert` updates без `order_position` в payload → позиция НЕ меняется (verify через GET).

### 6. Review-changes артефакты
**Исполнитель:** `/fastapi-api-developer`
- `reviews/2026-05-21-tasks-order-position-stage2.md`.
- `reviews/2026-05-21-tasks-order-position-stage2.diff`.

## Контракт навигации
N/A — backend API only.

## Запрещённые элементы управления
N/A.

## Frontend Routes
Никаких изменений на frontend в этом PR.

## API Endpoints

| Endpoint | Method | Изменение |
|---|---|---|
| `POST /api/v1/tasks` | POST | Body: добавлено `order_position: int \| null`. Default — null. Не breaking. |
| `PATCH /api/v1/tasks/{task_id}` | PATCH | Body: добавлено `order_position: int \| null`. Default — null = не менять. Не breaking. |
| `GET /api/v1/tasks/{task_id}` | GET | Response: добавлено `order_position: int \| null`. Не breaking (новое поле). |
| `GET /api/v1/tasks/by-course/{course_id}` | GET | Response: добавлено `order_position` в TaskRead. Не breaking. Порядок — уже фиксирован в ТЗ-1. |
| `POST /api/v1/tasks/bulk-upsert` | POST | Body items: добавлено `order_position: int \| null`. Default — null = в конец / не менять. Не breaking. |
| `GET /api/v1/tasks/search` | GET | Без изменений. Поле `order_position` будет в response через TaskRead, сортировка остаётся по id. |
| `POST /api/v1/tasks/find-by-external` | POST | Без изменений. |
| `POST /api/v1/tasks/validate` | POST | Без изменений (валидация не проверяет order_position — поле опционально). |

## Concurrency & Idempotency

- **`POST /tasks` с `order_position=K`:** триггер сдвигает остальные. Параллельные запросы → две задачи могут стать на одну позицию K? Нет — каждая транзакция блокирует строки своего course_id через UPDATE сдвига; вторая дождётся.
- **`PATCH /tasks/{id}` с новым order_position:** идемпотентно по значению (повторный PATCH с тем же N → триггер видит `old=new` → no-op).
- **`bulk-upsert`:** последовательный обход items внутри одной транзакции (текущее поведение). При параллельных bulk-upsert'ах в один курс возможен interleaving позиций, но финальное состояние — валидное (нет дубликатов из-за UPDATE-сдвига).
- **Idempotency key:** не вводится. Существующая семантика bulk-upsert (`external_uid` → UPSERT) сохраняет идемпотентность по uid.

## SQL formula verification
N/A — этот этап не вводит raw SQL.

## Stage Dependency Graph

| Stage | Status | BLOCKED_BY |
|---|---|---|
| ТЗ-2 / Этап 1: Pydantic schemas | new | ТЗ-1 merged в main (колонка существует) |
| ТЗ-2 / Этап 2: bulk_upsert | new | Этап 1 (TaskUpsertItem обновлён) |
| ТЗ-2 / Этап 3: openapi.json | new | Этапы 1+2 |
| ТЗ-2 / Этап 4: CB mirror | new | Этапы 1+2 |
| ТЗ-2 / Этап 5: API smoke tests | new | Этапы 1+2 |
| ТЗ-2 / Этап 6: Review artefacts | new | Этап 5 PASS |

## Критерии приёмки

1. `pytest tests/test_tasks_api_order_position.py -v` PASS.
2. Полный suite `pytest tests/` без новых регрессий.
3. `curl -s http://localhost:8000/api/v1/tasks/{id} | jq .order_position` возвращает число для существующих tasks (после ТЗ-1).
4. `POST /tasks` с явным `order_position` создаёт задачу на нужной позиции; MCP-проверка существующих в курсе показывает сдвиг.
5. `PATCH /tasks/{id}` с `order_position` меняет позицию; через GET виден новый порядок.
6. `POST /tasks/bulk-upsert` с миксом NULL/значение даёт ожидаемое распределение.
7. `docs/openapi.json` содержит `order_position` в TaskCreate/TaskUpdate/TaskRead/TaskUpsertItem.
8. CB mirror commit запушен; CHANGELOG обновлён.
9. `/tasks/search` продолжает сортировать по id (regression negative-test).

## Команды проверки

```powershell
cd D:\Work\LMS
.\venv\Scripts\activate
$env:PYTHONIOENCODING="utf-8"

# 1. API smoke
pytest tests/test_tasks_api_order_position.py -v

# 2. Full suite
pytest tests/ -x

# 3. OpenAPI diff
git diff docs/openapi.json | findstr /C:"order_position"
# ожидание: 4 группы upserts/creates с order_position

# 4. Live API smoke (требуется работающий uvicorn + auth token)
# создаём новую задачу в тестовом курсе:
$body = @{
  task_content = @{type='SC'; stem='test'; options=@(@{id='a';label='1'},@{id='b';label='2'})}
  course_id = <TEST_COURSE_ID>
  difficulty_id = <DIFF_ID>
  solution_rules = @{type='SC'; correct_options=@('a'); max_score=1}
  order_position = 1
} | ConvertTo-Json -Depth 10
curl -X POST http://localhost:8000/api/v1/tasks -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -d $body

# 5. Проверка сдвига через MCP
# SELECT id, course_id, order_position FROM tasks WHERE course_id=<TEST_COURSE_ID> ORDER BY order_position;
```

## Артефакты review-gate

- `reviews/2026-05-21-tasks-order-position-stage2.md`.
- `reviews/2026-05-21-tasks-order-position-stage2.diff`.
- Pytest summary для `test_tasks_api_order_position.py`.
- `docs/openapi.json` diff (фрагмент с order_position).
- Snapshot ответов `GET /tasks/by-course/{id}` до и после bulk-upsert с явными позициями.

## Переиспользование общей инфраструктуры

| Что переиспользуем | Откуда | Как |
|---|---|---|
| Шаблон расширения TaskUpsertItem | `Materials` bulk-upsert (если есть) или общий паттерн | Просто Optional поле + проброс |
| TaskRead полный паттерн | Существующий TaskRead с `model_config(from_attributes=True)` | Добавить поле, ничего больше |
| Smoke-тесты | `tests/test_materials_api_smoke.ps1` (если есть аналог) | По мотивам |

## Preflight / Deployment Checklist

- [ ] ТЗ-1 merged в `main` и применён в локальной БД (alembic upgrade head выполнен).
- [ ] `pytest tests/test_tasks_order_position.py` PASS на ветке `main`.
- [ ] Локальный dev-сервер запускается без warning'ов.
- [ ] Доступен валидный JWT-токен для smoke API.

## Live smoke test (после deploy)

```powershell
$env:TASKS_API_LIVE_SMOKE="1"
pytest tests/test_tasks_api_order_position.py::test_live_smoke_post_patch_bulkupsert -v
```
Контент `test_live_smoke_post_patch_bulkupsert` (gated):
1. POST /tasks с `order_position=NULL` → ответ 201 + GET → `order_position == MAX+1`.
2. POST /tasks с `order_position=1` → 201 + GET по курсу → существующие сдвинуты на +1.
3. PATCH /tasks/{id} с `order_position=5` → 200 + GET → позиция 5.
4. POST /tasks/bulk-upsert смесь NULL/2/4 → 200 + GET по курсу → детерминированное распределение.
5. Cleanup созданных задач.

## Артефакты передачи

- Бранч: `feature/tsk-004-tasks-order-position-stage2`.
- Commit messages: `feat: добавить order_position в схемы TaskCreate/Update/Read/UpsertItem`, `feat: проброс order_position в bulk_upsert`, `docs: regenerate openapi.json с order_position`, `test: smoke API тесты для tasks.order_position`, `cross-project: LMS tasks API — order_position в схемах` (отдельный CB commit).
- После merge — перевести `tsk-004` Этап 1.6 в `done` с финальным комментарием в «История движения».

## Риски и откат

| Риск | Уровень | Митигация |
|---|---|---|
| TaskUpdate.order_position=None трактуется как «обнулить» вместо «не менять» | Средний | Тест T19; явный if data.get(...) is not None в bulk_upsert UPDATE ветви |
| Изменение TaskRead ломает SPW/TG_LMS старых клиентов (extra field) | Низкий | Pydantic клиенты обычно игнорируют unknown fields; новое поле — не breaking |
| Regeneration openapi.json захватывает другие изменения | Низкий | Diff проверять вручную перед коммитом |
| Race PATCH × PATCH в один task | Низкий | Триггер всё равно консистентен (UPDATE-сдвиг последовательный) |
| Bulk-upsert с явными order_position в большом количестве — O(N²) | Низкий | Документировано в контракте (ТЗ-1, раздел 14) |

**Откат:**
```powershell
git revert <commit-hash>  # удалит поле из схем
git -C D:\Work\ContentBackbone revert <cb-commit>
# триггеры и колонка из ТЗ-1 остаются — клиенты просто не смогут передать order_position
```
