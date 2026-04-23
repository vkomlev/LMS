# Review: Фикс контракта attempt.meta.task_ids (Learning API)

**Дата:** 2026-02-26

**Контекст:** По ТЗ [tz-learning-engine-fix-attempt-meta-task-ids.md](docs/tz-learning-engine-fix-attempt-meta-task-ids.md) устранено расхождение: после `start-or-get-attempt` в `GET /attempts/{id}` поле `attempt.meta.task_ids` иногда было пустым.

**Изменения:**

1. **app/services/attempts_service.py**
   - Добавлен метод `ensure_attempt_task_ids(db, attempt, task_id) -> Attempts`:
     - Приводит `attempt.meta` к объекту (при null/не dict — новый dict, WARN в лог).
     - Приводит `meta.task_ids` к списку int (при не list — пустой список, WARN).
     - Фильтрует элементы только типа int.
     - Добавляет `task_id` в список без дублей.
     - Сохраняет через `update(db, attempt, {"meta": meta})` и возвращает обновлённую попытку.

2. **app/api/v1/learning.py** (start-or-get-attempt)
   - Для **существующей** активной попытки: перед возвратом вызывается `ensure_attempt_task_ids(db, existing, task_id)`, затем commit и ответ.
   - Для **новой** попытки: `create_attempt(..., meta={"task_ids": [task_id]})`, затем `ensure_attempt_task_ids(db, attempt, task_id)` (идемпотентно), commit и ответ.

3. **Тесты**
   - **tests/test_attempt_meta_task_ids.py**: unit-тесты логики ensure (mock update):
     - meta=None → task_ids=[X];
     - task_ids=[] → добавляется X;
     - X уже в списке → без дубля;
     - task_ids=[Y], X≠Y → merge [Y, X];
     - нормализация: только int в task_ids.

4. **Документация**
   - **docs/assignments-and-results-api.md**: в ответе GET /attempts/{id} в примере `meta` дополнен `task_ids: [1,2,3]`; добавлен абзац «Инвариант Learning API (start-or-get-attempt)» с гарантией по `meta` и `meta.task_ids`.
   - **docs/api-reference.md**: в таблице Learning API к строке start-or-get-attempt добавлена гарантия по `meta.task_ids`; в примере ответа попыток `meta` заменён на `{"task_ids": [123]}`.

**Проверки:** `python tests/test_attempt_meta_task_ids.py` — 5/5 PASS.

Полный diff: [2026-02-26-attempt-meta-task-ids.diff](2026-02-26-attempt-meta-task-ids.diff)
