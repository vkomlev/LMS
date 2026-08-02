# tsk-541: student_course_state — фоновый пересчёт подкурсов при добавлении course_dependencies

Дата: 2026-08-03
Источник: [[tsk-523]] (курс 88, найдено и закрыто вручную бэкфиллом 340 строк, системный
хвост передан `/fastapi-api-developer`, задача заведена только сейчас — tsk-541).

## Контекст и диагноз

`student_course_state` — кеш прогресса, на который смотрит
`me_service._BLOCKED_COURSES_SQL`: подкурс считается заблокированным, если в кеше НЕТ строки
`state='COMPLETED'` для его `required_course_id`. Строку пишет только
`LearningEngineService.compute_course_state(update_state_table=True)`.

По коду подтверждено — эту функцию вызывают ровно два места, и оба пишут кеш только для
**корня**:
1. `resolve_next_item` (`learning_engine_service.py:800`) — считает зависимости ТОЛЬКО
   `current_root_id` (корень из `user_courses`), при каждой навигации «дальше».
2. `manual_progress_service._refresh_course_state` — после ручной проверки/зачёта считает
   `compute_course_state` только для «активных корней touched-узла»
   (`list_active_roots_of_node`), не для промежуточных подкурсов-контейнеров.

Если `course_dependencies` ссылается на ПОДКУРС (`course_id`/`required_course_id` внутри
одного дерева, не корень) — кеш для него не пишет ни один из этих путей никогда. Новая
зависимость молча блокирует всех активных студентов по этому подкурсу, даже уже прошедших
пререквизит. Ровно это произошло в tsk-523 (34 студента × 10 подкурсов курса 88, 340
строк — 0 до инцидента).

**Важное уточнение диагноза, изменившее план фикса.** По `reviews/2026-08-02-tsk523-course88-
fixes.md` подтверждено: сами 9 строк `course_dependencies` курса 88 были записаны **прямым
SQL-скриптом** (`tsk523_apply.py`) под протоколом `/db-check`, в обход API/сервисного слоя
целиком. Значит фикс только в `CourseDependenciesService` (синхронный бэкфилл при API-записи)
не покрыл бы фактический путь регрессии — нужен ещё механизм, не зависящий от того, как
физически появилась строка `course_dependencies`.

## Решение (скомбинированы варианты 1 и 2 из декомпозиции; детектор — вариант 3 — не строился)

1. **Синхронный бэкфилл при API-записи** (`app/services/course_dependencies_service.py`):
   `add_dependency`/`bulk_add_dependencies` после записи зовут новый
   `LearningEngineService.backfill_dependency_state(course_id, required_course_id)` — тот
   находит активных студентов, у кого `course_id` входит в дерево активного корня
   (`list_active_students_with_node_in_tree`, зеркало существующего `list_active_roots_of_node`
   в обратную сторону), и пересчитывает для каждого `compute_course_state(required_course_id,
   update_state_table=True)`. Закрывает окно молчания для методиста, работающего через кабинет.
2. **Фоновый APScheduler-тик** (`app/services/course_dependency_state_cron_service.py`,
   новый файл) — тот же паттерн, что у соседних тиков (`escalation_service`,
   `lesson_attendance_cron_service`, `link_audit_service`): PG advisory lock (свой ключ,
   ascii `CDST`), `session_factory` для тестовой инъекции, `start_scheduler`/`stop_scheduler`,
   зарегистрирован в `app/api/main.py` (startup/shutdown). Раз в `COURSE_DEPENDENCY_STATE_
   CRON_INTERVAL_MIN` (default 15) минут проходит ВСЕ различные пары `(course_id,
   required_course_id)` из `course_dependencies`, для каждой находит активных студентов узла
   `course_id` и пересчитывает `required_course_id`; пары с общим `required_course_id`
   дедуплицируются по множеству студентов до пересчёта. **Это единственная защита для пути
   записи в обход API** — ровно того, которым была внесена сама регрессия tsk-523.
   Включение/выключение — `COURSE_DEPENDENCY_STATE_CRON_ENABLED` (default true).
3. **Детектор (вариант 3) сознательно не строился**: фоновый тик уже самовосстанавливающийся
   (пересчитывает состояние заново каждый интервал, не только сообщает о расхождении), поэтому
   отдельный read-only скрипт добавил бы наблюдаемость, но не закрыл бы риск сильнее, чем уже
   закрывают пп. 1-2. Задача явно оставляла это на усмотрение («по желанию как страховка
   сверху»).

## Changed Files

- `app/services/learning_engine_service.py` — `_ACTIVE_STUDENTS_WITH_NODE_SQL` (зеркало
  `_ACTIVE_ROOTS_OF_NODE_SQL`), методы `list_active_students_with_node_in_tree`,
  `backfill_dependency_state`.
- `app/services/course_dependencies_service.py` — `add_dependency`/`bulk_add_dependencies`
  зовут бэкфилл после записи.
- `app/services/course_dependency_state_cron_service.py` (новый) — фоновый тик.
- `app/core/config.py` — `course_dependency_state_cron_enabled`,
  `course_dependency_state_cron_interval_min`.
- `app/api/main.py` — регистрация startup/shutdown хуков тика.
- `tests/test_tsk541_subcourse_dependency_state_backfill.py` (новый) — 5 regression-тестов.

Не тронуты (вне охвата, возможна параллельная работа других чипов): `payment_service.py`,
`charge_service.py`, `teacher_reviews.py`, `teacher_queue_service.py`.

## Regression-тесты (`tests/test_tsk541_subcourse_dependency_state_backfill.py`)

Синтетический граф: root → child_a, child_b (дети через `course_parents`), по одному заданию
в каждом ребёнке; студент записан только на root (как в проде — подкурсы не enroll'ятся
напрямую).

1. `test_add_dependency_backfills_completed_prerequisite` — студент прошёл child_a ДО записи
   зависимости child_b→child_a; после `add_dependency` кеш = COMPLETED, child_b разблокирован.
2. `test_add_dependency_backfill_keeps_incomplete_prerequisite_blocked` — бэкфилл считает
   РЕАЛЬНОЕ состояние (NOT_STARTED), не слепо проставляет COMPLETED.
3. `test_bulk_add_dependencies_backfills_state` — тот же бэкфилл для массового эндпоинта.
4. `test_backfill_scoped_to_active_students_of_gated_course` — студент вне дерева child_b кеш
   не получает (страховка от переразмашистого пересчёта).
5. `test_background_tick_backfills_state_for_dependency_added_via_raw_sql` — **ключевой**:
   `course_dependencies` записана ПРЯМЫМ SQL (тот же способ, что в tsk-523), синхронный
   бэкфилл сервиса эту запись не видит вообще; кеш появляется только после
   `course_dependency_state_cron_tick`.

**Верификация «тест ловит регрессию»**: все 5 тестов прогнаны против кода ДО фикса (`git
stash` на изменённые файлы + новый файл убран из дерева) — 4 из 5 упали (пятый, страховка от
переразмашистости, по конструкции проходит в обоих состояниях — он проверяет отсутствие
записи, а не сам факт бэкфилла). После восстановления фикса — все 5 зелёные.

## Validation Commands

```
"./.venv/Scripts/python.exe" -m pytest -q
```

- **До фикса** (baseline, чистый `main`): `1534 passed, 11 skipped`.
- **После фикса**: `1539 passed, 11 skipped` (+5 — новые тесты tsk-541, 0 регрессий).

## DB Findings (MCP `learn_prod_db`, read-only)

Живой прогон диагностического запроса (та же формула, что использует фоновый тик: активные
студенты дерева `course_id` × наличие строки `student_course_state[required_course_id]`)
показал: **дефект живёт на проде прямо сейчас, не только в истории курса 88**.

Кроме уже известных 9 пар курса 88 (там `missing=0` — закрыто ручным бэкфиллом tsk-523),
найдены дырки:

- **Курс 112 «ЕГЭ по информатике» → курс 88** (root-level, само по себе самовосстанавливается
  через `resolve_next_item` при навигации, но фоновый тик закрывает окно и для него):
  32 активных студента, 25 имеют строку кеша, **7 не имеют**.
- **Курс 1246/1247** («Сводная практика») — 4+4 пары зависимостей между подкурсами,
  **0 из 2 активных студентов** имеют строку кеша по каждой паре.
- **Курсы 1270-1274** (Excel) — 5 пар, **0 из 2** студентов.
- **Курсы 1281/1282** (пробные тесты) — 2 пары, **0 из 2** студентов.
- **Дерево «Тестировщик» (курсы 1284→1420, ~25 пар последовательных зависимостей глав)** —
  **0 из 1-2** студентов по каждой паре. Это самый крупный из найденных: цепочка из 17 глав,
  каждая следующая зависит от предыдущей, и НИ ОДНА строка `student_course_state` для этих
  подкурсов не существует — то есть студенты этого трека потенциально видят заблокированными
  главы, которые прошли давно.

Итого: не единичный инцидент курса 88, а системный класс, актуальный на нескольких
курсах прямо сейчас. Деплой фонового тика (и его первый прогон, интервал 15 мин) закроет всё
перечисленное автоматически, без ручного бэкфилла.

## Risks / Follow-ups

- Производительность тика: на каждую различную пару `(course_id, required_course_id)` из
  `course_dependencies` выполняется отдельный рекурсивный обход АКТИВНЫХ `user_courses` (не
  ограниченный конкретным деревом до WHERE в конце). При нынешнем масштабе LMS (десятки-сотни
  активных записей) это дёшево; если `course_dependencies` вырастет на порядок — стоит
  добавить индекс/материализацию или схлопнуть обход в один SQL-запрос по всем парам сразу.
  Не блокирует эту задачу — зафиксировано как наблюдение.
- Обнаруженный масштаб дырки на курсах 1284-1420 («Тестировщик») больше, чем ожидалось из
  декомпозиции задачи (которая называла курс 88/112 как пример). Оператору стоит знать: после
  деплоя эти студенты могут ОДНОМОМЕНТНО получить доступ к главам, которые уже прошли — это
  ожидаемое, желаемое поведение фикса, а не побочный эффект, но стоит быть готовым к вопросу
  «почему вдруг разблокировалось».
- Детектор (`scripts/*_invariant.py`, вариант 3 декомпозиции) не построен — см. обоснование
  выше. Если оператор захочет отдельную read-only наблюдаемость (например, для алерта до
  первого тика, а не после) — можно добавить позже по образцу
  `scripts/materials_junk_invariant.py`.

## Operator handoff

Коммит + пуш + деплой — ветвь А (durable-авторизация, `operator-handoff-rules.md`): review-gate
пройден (ПРИНЯТО), тесты зелёные → коммичу и деплою сам, с обязательной живой проверкой в
браузере/через MCP после деплоя.
