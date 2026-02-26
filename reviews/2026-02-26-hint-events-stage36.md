# Learning Engine V1, этап 3.6 (Hint events)

**Дата:** 2026-02-26

**Контекст:** Реализация ТЗ [tz-learning-engine-stage3-6-hint-events.md](../docs/tz-learning-engine-stage3-6-hint-events.md): фиксация открытия подсказок (text/video), дедуп, расширение статистики, тесты и документация.

**Изменения:**

1. **learning_events_service**
   - `record_hint_open(db, student_id, attempt_id, task_id, hint_type, hint_index, action, source)` — запись в `learning_events` с `event_type='hint_open'`, payload (attempt_id, task_id, hint_type, hint_index, action, source). Advisory lock по (attempt_id, task_id), дедуп по ключу (attempt_id, task_id, hint_type, hint_index, action) в окне 5 мин. Возврат (event_id, deduplicated).
   - `get_hint_open_counts(db, task_id=None, user_id=None, task_ids=None)` — подсчёт событий hint_open (total, text_count, video_count) для stats.

2. **Схемы (learning_api.py)**
   - `HintEventRequest`: student_id, attempt_id, hint_type (text|video), hint_index (ge=0), action ("open"), source.
   - `HintEventResponse`: ok, deduplicated, event_id.

3. **API (learning.py)**
   - `POST /learning/tasks/{task_id}/hint-events`: валидация task/student/attempt; проверка attempt.user_id == student_id, attempt.course_id == task.course_id, task_id in attempt.meta.task_ids (если есть). 404/409 по ТЗ. Вызов record_hint_open, commit, ответ 200.

4. **Статистика (task_results_service)**
   - `get_stats_by_task`: добавлены поля hints_used_count, used_text_hints_count, used_video_hints_count (через get_hint_open_counts по task_id).
   - `get_stats_by_user`: те же три поля (по user_id).
   - `get_stats_by_course`: те же три поля (по task_ids курса).

5. **Тесты**
   - `tests/test_hint_events_stage36.py`: успешная запись (deduplicated=false), повтор в окне дедупа (deduplicated=true), HTTP 409 при чужом student_id, наличие трёх полей в by-task/by-user, регрессия типов полей stats.

6. **Документация**
   - api-reference.md: таблица Learning API (строка hint-events), раздел POST hint-events, в примерах stats добавлены hints_used_count, used_text_hints_count, used_video_hints_count.
   - assignments-and-results-api.md: поля stats, абзац про POST hint-events и ссылки.
   - api-examples.md: ссылка на ТЗ этапа 3.6, раздел с примером curl для hint-events.

**Примечание:** docs/openapi.json в проекте не найден; контракт доступен через Swagger UI (/docs) и описан в api-reference.

**Полный diff:** [2026-02-26-hint-events-stage36.diff](2026-02-26-hint-events-stage36.diff)
