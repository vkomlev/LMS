# tsk-088 — TaskContent schema объявил hints_text/hints_video/has_hints

**Дата:** 2026-05-24
**Задача:** [tsk-088](D:/Work/Root/tasks/tsk-088-lms-task-content-hints-schema.md) (P1, BLOCKER для tsk-004 Phase 6.7)
**Skill:** `/executor-pro`
**Статус:** готово к `/review-gate`

---

## Цель правки

LMS-side BLOCKER для Phase 6.7. tsk-004 Phase 6.6 pilot (CB, 2026-05-24) — live sync 7 polyakov-задач в LMS — выявил, что `task_content.hints_video` (VK-разборы Виктора, главная ценность tsk-004) не сохраняется в LMS, хотя pipeline отправляет корректный payload.

**Root cause:** `app/schemas/task_content.py:TaskContent` (Pydantic v2 BaseModel) не объявлял поля `hints_text/hints_video/has_hints`. Default `extra='ignore'` → при `TaskContent.model_validate(...).model_dump()` в `tasks_service.create/update/bulk_upsert` (3 точки потери) неизвестные поля silently отбрасывались, и в jsonb-колонку `tasks.task_content` записывалась урезанная версия.

**Цель (по AC tsk-088, вариант A — рекомендованный):** добавить 3 типизированных поля в `TaskContent`, чтобы model_dump их сохранял. Backward compat — дефолты пустые списки и `False`.

---

## Затронутые файлы и контракты

### Код (LMS)

- `app/schemas/task_content.py` — добавлены 3 поля в `class TaskContent`:
  - `hints_text: List[str] = Field(default_factory=list, …)`
  - `hints_video: List[str] = Field(default_factory=list, …)`
  - `has_hints: bool = Field(default=False, …)`
- `tests/test_tsk088_task_content_hints_preserved.py` — новый regression test (5 кейсов: unit round-trip, defaults, video-only, e2e bulk_upsert в БД, TaskRead derive после bulk_upsert).

### Контракты (cross-project, ContentBackbone)

- `D:/Work/ContentBackbone/docs/cross-project/contracts/lms-api.md` — новая секция `TaskContent` schema — hints поля (tsk-088 FIXED 2026-05-24)` + bump `Last verified: 2026-05-24`.
- `D:/Work/ContentBackbone/docs/cross-project/CHANGELOG.md` — append запись tsk-088 в начало (Project/Change/Impact/Action/Authority/Refs).
- `STATE.md` — без изменений (bugfix внутри уже задокументированной фазы Y-6.2).

### Трекер (Root)

- `D:/Work/Root/tasks/tsk-088-lms-task-content-hints-schema.md` — статус `backlog → active` (закрытие до `done` — после PASS review-gate и commit'a).

---

## Изменение публичного контракта

`task_content` — поле в payload эндпоинтов:

- `POST /api/v1/tasks` (TaskCreate.task_content)
- `PATCH /api/v1/tasks/{id}` (TaskUpdate.task_content)
- `POST /api/v1/tasks/bulk-upsert` (TaskUpsertItem.task_content)
- `GET /api/v1/tasks` / `GET /api/v1/tasks/{id}` (TaskRead.task_content)
- `POST /api/v1/tasks/validate` (TaskValidateRequest.task_content)
- `POST /api/v1/tasks/import/google-sheets` (внутри)

До правки `TaskContent` молча отбрасывал hints поля. После правки — сохраняет. Это **расширение** контракта (новые поля **опциональны, с дефолтами**), backward compat для consumers, не передающих hints. **MANDATORY review-gate triggered:** изменение публичной schema — поэтому шаг 4.5 (cross-project backsync) выполнен в том же коммите.

---

## Результаты валидации

### Regression strength check (доказательство, что тест ловит баг)

Откатил фикс `app/schemas/task_content.py` через `git stash`, прогнал unit-тест:

```
FAILED tests/test_tsk088_task_content_hints_preserved.py::test_task_content_preserves_hints_round_trip
======================== 1 failed, 4 warnings in 0.96s ========================
```

После восстановления фикса (`git stash pop`):

```
tests/test_tsk088_task_content_hints_preserved.py::test_task_content_preserves_hints_round_trip PASSED
tests/test_tsk088_task_content_hints_preserved.py::test_task_content_defaults_when_hints_absent PASSED
tests/test_tsk088_task_content_hints_preserved.py::test_task_content_preserves_only_video_hints PASSED
tests/test_tsk088_task_content_hints_preserved.py::test_bulk_upsert_preserves_hints_video_in_db PASSED
tests/test_tsk088_task_content_hints_preserved.py::test_task_read_derives_has_hints_after_bulk_upsert PASSED
======================== 5 passed in 2.04s ========================
```

### Полная связка (regression + смежные)

```
python -m pytest tests/test_tsk088_task_content_hints_preserved.py \
                 tests/test_hints_stage5.py \
                 tests/test_tasks_order_position_api.py \
                 tests/test_tasks_reorder_api.py
…
======================= 30 passed, 4 warnings in 17.09s =======================
```

Смежные тесты (hints derive в TaskRead, bulk_upsert order_position, tasks reorder) не сломаны.

### MCP DB / Log Findings

Не требуется — фикс на уровне Pydantic schema, не затрагивает DDL, миграции или SQL.
AC-6 (verify через CB pilot retry на tasks 1473-1479) — выполнит оператор после deploy LMS-side и `pilot apply-answers --collect-hints` (см. раздел Operator handoff ниже).

### Date/Type Guard Evidence

Не применимо (нет изменений в date/SLA/TTL логике).

---

## Acceptance criteria — состояние

- [x] **AC-1:** TaskContent объявляет hints_text/hints_video/has_hints с дефолтами (вариант A).
- [x] **AC-2:** `_extract_hints_from_task_content` (`app/schemas/tasks.py:8-26`) работает прежним способом — формат `List[str]` совместим.
- [x] **AC-3:** `TaskRead.fill_hints_from_task_content` validator продолжает derive (доказано тестом `test_task_read_derives_has_hints_after_bulk_upsert`).
- [x] **AC-4:** Regression test e2e: bulk_upsert с `hints_video=["https://vk.com/video-220754053_456239998"]` → SELECT task_content из БД содержит hints_video.
- [x] **AC-5:** Backward compat — дефолты `[], [], False` для тасков без hints (доказано `test_task_content_defaults_when_hints_absent`).
- [ ] **AC-6:** CB pilot retry (`pilot apply-answers --collect-hints` → `sync --confirm-live`) → tasks 1473-1479 покажут hints_video. **Operator-action** (категория Б): требует деплой LMS-side fix и ручной запуск CB pipeline.

---

## Spec Test Coverage Audit

Spec tsk-088 не содержит секции «Tests»/«Test Coverage» с явным списком файлов. AC-4 требует **regression test** — реализован в `tests/test_tsk088_task_content_hints_preserved.py` (5/5 pass, e2e ветка покрывает AC-4 буквально). AC-3 и AC-5 покрыты separate-тестами в том же файле. AC-6 — оператор-side, вне scope автоматизированного покрытия.

Источник риска LMS ERRORS 2026-04-29 #1 (пропуск файла из spec §«Tests») не применим — здесь spec без явного списка файлов, и единственный нужный regression-файл создан и прогнан.

---

## Review artifact

- `reviews/2026-05-24-tsk088-task-content-hints.md` (этот файл)
- `reviews/2026-05-24-tsk088-task-content-hints.diff` (из `git diff --cached`)

---

## Rollback note

1. `git revert <commit>` (откатывает schema + regression test одной операцией).
2. Или вручную: удалить 3 поля из `TaskContent` — `Field(default_factory=list, …)` дефолты обеспечивают совместимость consumers, передающих hints (они снова станут silently dropped — это и есть исходное состояние).
3. Cross-project backsync (lms-api.md + CHANGELOG.md) — оставить как историческую запись (append-only), либо отдельным коммитом в CB добавить erratum.
4. **Воздействие отката:** возвращается старый баг — tsk-004 Phase 6.7 снова заблокирована. Других последствий нет (нет миграций, нет данных, которые нужно бы было откатить).

---

## Operator handoff (для AC-6 после ship'a)

**Категория А** (выполнил сам): валидация, regression test, cross-project backsync, review artifact.

**Категория Б** (требуется оператор после merge + deploy LMS):

1. Restart LMS API (если запускается отдельным процессом оператора): остановить текущий процесс, перезапустить из `D:\Work\LMS` (`.\.venv\Scripts\python -m uvicorn app.api.main:app --reload --port 8000` или ваш стандартный запуск).
2. CB pilot retry:
   ```
   cd D:\Work\ContentBackbone
   python -m pilot apply-answers --collect-hints
   python -m pilot sync --confirm-live
   ```
3. Проверить tasks 1473-1479 (MCP postgresql, read-only):
   ```sql
   SELECT id, external_uid,
          task_content->'hints_video' AS hints_video,
          task_content->'hints_text' AS hints_text
   FROM tasks WHERE id BETWEEN 1473 AND 1479
   ORDER BY id;
   ```
   Ожидаемый результат: `hints_video` непустой массив для тасков, у которых были VK-разборы в источнике.
4. После подтверждения (или при отсутствии hints в source — диагностика отдельно) — закрыть tsk-088 (статус `done`, добавить ссылку на commit'ы в раздел «История движения»).

**Что я (агент) делаю по возвращении:** перевожу tsk-088 в `done`, фиксирую `closed_at`, добавляю commit-refs в трекер.

---

## Risks / Follow-ups

- **Риск 1 (низкий):** Если где-то ниже по стеку (тесты валидации task_content) есть проверки, что `model_dump()` возвращает ровно N полей — могут начать падать. Прогнал смежные suite — не нашёл.
- **Риск 2 (низкий):** CB pipeline передаёт `hints_video=[]` и `hints_text=[]` для всех тасков (даже без hints) — теперь они будут persisted как пустые массивы вместо отсутствия ключа. Для consumers это no-op (extract_hints_from_task_content нормализует), но в jsonb-сравнении старых/новых записей может появиться визуальная разница. Не блокер.
- **Follow-up:** AC-6 (pilot retry) — внутри оператор-инструкции выше.
- **Follow-up:** Phase 6.7 full migration (1040 tasks) — отдельная задача tsk-004 Phase 6.7, разблокирована этим фиксом.
- **Follow-up:** SPW UI rendering hints — отдельная задача (упомянута в tsk-088 §«Не входит в scope»).
