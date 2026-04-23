# Review: Этап 4 — P1/P2 правки (таймаут, finish без ответов, тесты)

**Дата:** 2026-02-24

**Контекст:** Правки по ревью этапа 4: (1) P1 — при таймауте в answers попытка завершается (finished_at); (2) P1 — в finish дедлайн проверяется и по курсу при отсутствии task_results; (3) P2 — тест по сценарию ТЗ (time_limit_sec, просрочка, finish → time_expired и finished_at).

**Изменения:**

1. **P1. POST /attempts/{id}/answers при просрочке**  
   Вместо `set_time_expired` вызывается **finish_attempt(db, attempt.id, time_expired=True)**. Попытка получает и **time_expired=true**, и **finished_at**, перестаёт быть «активной».

2. **P1. POST /attempts/{id}/finish — дедлайн без ответов**  
   Список задач для проверки дедлайна: сначала из **task_results** попытки; если пусто и задан **course_id** — все задачи курса с **time_limit_sec is not null**. Логика вынесена в **AttemptsService**:  
   - **\_get_task_ids_for_deadline_check(db, attempt_id, course_id)**  
   - **check_attempt_deadline_expired(db, attempt)**  
   Эндпоинт finish вызывает `check_attempt_deadline_expired` и по его результату передаёт **time_expired** в **finish_attempt**.

3. **P2. Тесты**  
   Добавлен **test_time_expired_finish_attempt**: создаётся попытка по курсу с задачей с **time_limit_sec**, **created_at** сдвигается в прошлое (на 2 мин), проверяется **check_attempt_deadline_expired=True**, вызывается **finish_attempt(time_expired=True)**, проверяются **time_expired** и **finished_at**. При отсутствии в БД задачи с **time_limit_sec** тест помечается как skip.

4. **Документация**  
   В **docs/attempts-integration-stage4.md** уточнено: при просрочке в answers вызывается finish_attempt; в finish дедлайн проверяется и по курсу при отсутствии ответов.

Полный diff: [2026-02-24-stage4-p1-p2-fixes.diff](./2026-02-24-stage4-p1-p2-fixes.diff)
