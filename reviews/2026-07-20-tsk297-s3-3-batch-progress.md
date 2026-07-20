# tsk-297, находка S3-3 — batch-расчёт статусов заданий на карточке ученика

Дата: 2026-07-20. Follow-up из `/review-gate` по tsk-297 (не блокирующая находка).

## Контекст

`manual_progress_service.get_student_progress` (карточка ученика в портале
преподавателя, `GET /api/v1/teacher/students/{id}/progress`) вызывал
`learning_engine_service.compute_task_state` в цикле по каждому заданию
дерева курса. Внутри `compute_task_state` — `get_effective_attempt_limit`
(до 3 запросов: тип задания/override/`tasks.max_attempts`) плюс запрос
счёта попыток и запрос последнего результата — итого ~5 запросов на задание.
На прод-курсе 871 (172 задания) это ~860 запросов на одно открытие карточки.

## Решение

Добавлен `LearningEngineService.compute_task_states_batch`
([app/services/learning_engine_service.py](../app/services/learning_engine_service.py))
— та же семантика статусов, что у `compute_task_state` (эта функция не
менялась и осталась источником истины для единичных вызовов next-item/attempts),
но батчем на весь список `task_ids`:

1. Один запрос: тип задания (квиз?) + `tasks.max_attempts` + override студента
   (`LEFT JOIN student_task_limit_override`) — определяет эффективный лимит.
2. Один запрос: счёт попыток, сгруппированный по `task_id`
   (`GROUP BY tr.task_id`, `root_course_id=None` — та же ветка, что у
   единственного вызывающего).
3. Последний результат по заданию — переиспользует уже загруженный
   `manual_progress_service.get_student_progress` ряд (тот и так нужен для
   флага `manual`/`granted_by`), колонки расширены (`attempt_id`, `score`,
   `max_score`, `answer_json`, `is_correct`) вместо повторного запроса.

Итого — 2 новых запроса на всё дерево вместо ~5 на задание. Ограничение
задокументировано явно в docstring: батч считает только ветку
`root_course_id=None` (как и единственный вызывающий сегодня); появится
вызывающий с конкретным корнем — расширять по образцу `compute_task_state`,
а не тянуть эту функцию молча.

`manual_progress_service.get_student_progress` теперь вызывает
`compute_task_states_batch` один раз перед циклом по дереву вместо
`compute_task_state` внутри цикла.

## Changed Files

- [app/services/learning_engine_service.py](../app/services/learning_engine_service.py) — новый метод `compute_task_states_batch`.
- [app/services/manual_progress_service.py](../app/services/manual_progress_service.py) — `get_student_progress`: расширен запрос `last_results`, добавлен один вызов `compute_task_states_batch`, цикл читает готовый статус вместо `await compute_task_state(...)` на каждой итерации.
- [tests/test_manual_progress_tsk297.py](../tests/test_manual_progress_tsk297.py) — тест эквивалентности `test_batch_task_states_match_individual_compute` (дерево passed/failed/blocked_limit/open+skipped, сверка batch vs поэлементно по всем полям `TaskStateResult` + по статусам в самой карточке) и `test_batch_task_states_empty_list_is_noop`.

> Diff в `reviews/2026-07-20-tsk297-s3-3-batch-progress.diff` — из `manual_progress_service.py`
> и тестового файла в него также попадают **чужие уже стоявшие в рабочем дереве
> незакоммиченные правки** (находка S3-2, квиз `manual_grantable`/`skipped_quiz`) —
> они не относятся к этой находке и не тронуты в этой сессии, оставлены как есть.
> В рабочем дереве параллельно шла ещё одна сессия (правки `teacher_progress.py`
> и новый `scripts/fix_order_position_lesson_group_tsk332.py`, не тронуто).

## Validation Commands

```
.venv/Scripts/python.exe -m pytest tests/test_manual_progress_tsk297.py -q
```

36 тестов зелёные (34 старых + 2 новых). При повторных прогонах подряд
наблюдались единичные несвязанные падения (`test_material_grant_and_revoke`,
`test_progress_tree_marks_quiz_not_grantable`, `test_progress_read_does_not_escalate`,
`test_real_completion_overrides_manual_provenance` — каждый раз другой тест) —
все зелёные изолированно; последний явно показал причину:
`ForeignKeyViolationError` на `student_teacher_links_student_id_fkey` — гонка
с параллельной сессией, писавшей/чистившей ту же dev-БД `Learn` одновременно.
Не связано с этой правкой: тесты `test_batch_task_states_*` ни разу не попали
в список падений ни в одном из 4 прогонов.

## Замер (до/после), реальный масштаб прод-курса 871

Синтетическое дерево на 172 задания (масштаб прод-курса 871: 22 узла, 172
задания) в локальной dev-БД, распределение PASSED/FAILED/BLOCKED_LIMIT/OPEN.
Замер — реальный вызов сервисного кода (`time.perf_counter()`, тот же async
драйвер/event loop, что и в проде), не синтетика:

| Путь | Время |
|---|---|
| СТАРЫЙ: `compute_task_state` в цикле (код метода не менялся) | **735.3 мс** |
| НОВЫЙ: `compute_task_states_batch` (2 запроса на дерево) | **7.8 мс** |
| НОВЫЙ: вся карточка `get_student_progress` целиком | **49.7 мс** |

Ускорение расчёта статусов заданий — **~95x**. Расхождений в статусах на
реальных данных (batch vs поэлементно) — 0 (проверено скриптом, синтетические
данные удалены после замера, dev-БД не засорена).

Замер локальный (localhost Postgres) — консервативная нижняя оценка: в проде
(отдельный процесс приложения) стоимость каждого дополнительного round-trip
обычно выше, чем на localhost, то есть реальный выигрыш от устранения ~860
последовательных запросов, вероятнее всего, больше измеренного.

## Risks / Follow-ups

- Батч сознательно не поддерживает `root_course_id != None` — см. docstring
  `compute_task_states_batch`. Единственный сегодняшний вызывающий
  (`get_student_progress`) в этом не нуждается.
- Правка не трогает `compute_task_state` (используется в `resolve_next_item`,
  `attempts.py` и других местах) — точечная, риск регрессии в остальном движке
  отсутствует.
