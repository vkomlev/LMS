# tsk-309 — детальный разбор ученика для teacher-бота (Шаг 2)

**Дата:** 2026-07-19
**Задача:** tsk-309 (интерим-видимость результатов до портала Фаза 3)
**Скиллы:** `/db-check` (привязка, прод), `/fastapi-api-developer` (эндпоинт), `/telegram-ux-flow-designer` (экран)
**Data Impact:** read (новый read-эндпоинт; миграций нет)

## Контекст

Шаг 1 (привязка Виктора id=2 к ученикам 4497/4498/4499 через `student_teacher_links`) выполнен и верифицирован ранее. Экраны teacher-бота показывали лишь общий свод + грубый список заданий («попыток: N, ср.балл»), без эталона оператора: **по каждому заданию — результат, с какой попытки сдал, баллы, подсказки; плюс свод по курсу**.

## Изменения

### LMS (backend)
- `app/services/task_results_service.py`
  - `get_detail_by_user(db, user_id, course_id=None)` — детальный разбор: per-task (is_correct, score/max, число попыток, **solved_on_attempt**, hints_used) + свод по корневым курсам + общий свод.
  - `_build_task_label(...)` — подпись задания (external_uid → title/stem → id), как в боте.
- `app/api/v1/task_results_extra.py`
  - `GET /task-results/detail/by-user/{user_id}?course_id=` — тонкий роутер, ACL идентична `/stats/by-user` (ученик — только свой разбор; service-key и роли admin/methodist/teacher — сквозной доступ).

**Ключевые семантические решения:**
- «С какой попытки сдал» = порядковый номер (1-based) первой ВЕРНОЙ строки `task_results` по паре (user, task), упорядоченной по `(submitted_at, id)`. Поле `count_retry` в данных = 0 у всех и не используется; повтор задания — это НОВАЯ строка результата (тот же attempt_id).
- Группировка по **корневому курсу** (`COALESCE(attempts.root_course_id, attempts.course_id)`), а не `tasks.course_id` — иначе подкурсы дробят свод (у Риты 74 задания → 6 подкурсов vs 1 корневой курс 1248).
- Учитываются только незакрытые попытки (`attempts.cancelled_at IS NULL`) — консистентно с остальной статистикой (этап 6).
- Баллы задания = лучший результат (`MAX(score)`), статус = `bool_or(is_correct)`.

### TG_LMS (teacher/methodist боты)
- `src/common/models.py` — модели `UserDetailRead` / `UserDetailOverall` / `UserDetailCourseSummary` / `UserDetailTaskItem`.
- `src/common/api_client.py` — `get_user_detail(user_id, course_id=None)` (auth через query `api_key`, как у `get_user_stats`).
- `src/common/services/task_results_service.py` — обёртка `get_user_detail`.
- `src/bots/common/dialogs/user_stats_base.py` — геттер `user_stats_task_list_getter` переписан на детальный эндпоинт; строка задания: `✅/❌ {label} · {score}/{max} · с N-й попытки · 💡K`; шапка — свод общий + по курсам. Удалён мёртвый код (`_task_label_from_content`, `USER_STATS_TASK_LIST_LIMIT`, N+1-загрузка меток через `tasks_service`).
- Экран доступен обоим ботам (teacher и methodist) — оба используют `user_stats_base`.

## Валидация (evidence)

Стек тестов недоступен для полного e2e без деплоя (прод API ещё без нового эндпоинта), поэтому валидация — прямым прогоном реального кода против прод-БД (read-only) + регрессия соседей.

1. **SQL-агрегация против прод (MCP + прямой прогон сервиса):**
   - Рита 4497: 74/74 решено, 129 попыток, 22 задания с повторами, макс solved_on_attempt=7; задание 7399 → attempts=2, solved_on_attempt=2 ✅.
   - Михаил 4498: 17/17 с 1-й попытки, 💡1.
   - Достан 4499: пустая структура без падения.
   - Фильтр course_id: 1248 → 74 задания/1 курс; несуществующий → 0.
2. **Сквозное превью экрана** (LMS-сервис → `UserDetailRead.model_validate` → рендер-хелперы бота): модель парсит ответ чисто; текст экрана точно соответствует эталону (статус/баллы/«со 2-й попытки»/💡, свод по курсу и общий).
3. **Регрессия:** `pytest tests/test_last_attempt_stage6.py tests/test_hint_events_stage36.py` → **22 passed**.
4. **Синтаксис:** `py_compile` всех 6 изменённых файлов — OK.

## Риски / follow-ups

- **Деплой:** эндпоинт аддитивный (read-only), но прод-API его пока не отдаёт — до деплоя LMS бот получит 404 (обрабатывается: «Не удалось загрузить разбор.»). Нужен деплой LMS + рестарт teacher/methodist-ботов.
- Длинные `external_uid` в подписи (как и в прежнем экране) — визуально громоздко; не трогал (паритет), можно укоротить позже.
- ListGroup без пагинации: у Риты 74 кнопки-задания (паритет с прежним экраном). При росте числа заданий — добавить пагинацию (follow-up).
- Богатый разбор с фильтрами/графиками — портал Фаза 3 (tsk-298), не дублировать в боте.
