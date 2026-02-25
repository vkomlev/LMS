# Review: Learning API — P1/P2 гонки и GET next-item

**Дата:** 2026-02-24

**Контекст:** Правки по ревью этапа 3: (1) P1 — concurrency-safe `start-or-get-attempt`; (2) P2 — атомарный дедуп `request-help`; (3) P2 — явное указание, что GET next-item пишет в БД.

**Изменения:**

1. **P1. start-or-get-attempt** ([learning.py](d:/Work/LMS/app/api/v1/learning.py))  
   Перед выборкой/созданием активной попытки вызывается `pg_advisory_xact_lock(user_id, course_id)`. Параллельные запросы для одной пары (студент, курс) сериализуются, вторая транзакция видит уже созданную попытку и возвращает её — дубликатов активных попыток не возникает.

2. **P2. request-help дедуп** ([learning_events_service.py](d:/Work/LMS/app/services/learning_events_service.py))  
   В начале `record_help_requested` вызывается `pg_advisory_xact_lock(student_id, task_id)`. Проверка дубликата и вставка выполняются в одной сериализованной по (student_id, task_id) транзакции, дубли событий при параллельных одинаковых запросах не создаются.

3. **P2. GET next-item**  
   В [learning.py](d:/Work/LMS/app/api/v1/learning.py) добавлен комментарий: GET выполняет запись (upsert `student_course_state` при проверке зависимостей), возможна write-амплификация. В [learning-engine-next-item.md](d:/Work/LMS/docs/learning-engine-next-item.md) добавлено примечание о том же и вариантах (отдельный endpoint/кэш для read-only).

Полный diff: [2026-02-24-learning-api-p1-p2-concurrency.diff](./2026-02-24-learning-api-p1-p2-concurrency.diff)
