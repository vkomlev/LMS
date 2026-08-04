# tsk-559 — фильтр активные/все/заблокированные (LMS backend)

## Контекст

Кабинет методиста, две независимые доделки (полная разведка и решения — см.
`D:\Work\Root\tasks\tsk-559-*.md`):

1. Фильтр «только активные / все / заблокированные» для материалов, заданий,
   людей. Курсы — пропущены: в БД (`app/models/courses.py`) нет ни `is_active`,
   ни поля архивации (проверено грепом моделей/миграций/схем + отдельно
   уточнено у оператора в этой сессии: заявление про «архивацию» было
   неточным памятью, курсы решено пропустить).
2. Навигация вперёд/назад в материале/задании методиста — чистый фронт SPW,
   бэкенд не менялся (`order_position` уже отдаётся сортировкой по умолчанию).

## Changed Files (LMS)

- `app/services/tasks_service.py` — `get_by_course` получил `is_active: bool | None = None`.
- `app/api/v1/tasks_extra.py` — `GET /tasks/by-course/{course_id}` получил query-параметр `is_active`.
- `app/repos/users_repo.py` — `list_with_role_filter` и `search_by_full_name_with_role` получили `blocked: bool | None = None` (фильтр по `blocked_at`, НЕ по `is_active`, который означает «слитая учётка», tsk-432).
- `app/services/users_service.py` — параметр `blocked` прокинут через сервисный слой.
- `app/api/v1/users.py` — `GET /users/` и `GET /users/search` получили query-параметр `blocked`.
- `tests/test_tsk559_active_blocked_filters.py` — новый файл, 9 тестов.

## DB Findings (MCP / read-only)

Проверка сделана по коду моделей (не через MCP — колонки видны прямо в
`Mapped[...]` аннотациях, доп. SQL не требовался):

- `tasks.is_active: bool` (`app/models/tasks.py:108`) — уже существовала,
  использована как есть.
- `users.blocked_at: datetime | None` (`app/models/users.py:62`) — уже
  существовала (tsk-432/433), использована как есть. `users.is_active`
  (`:51`) — другая ось («слит»), не используется этим фильтром.
- `courses` (`app/models/courses.py`) — нет `is_active`, нет archived-поля.
  Только `access_level` (enum из 5 режимов проверки), `is_required`,
  `is_public_demo`, `course_uid`, `sampling_config`. Подтверждено также
  grep по SPW/ContentBackbone — ни WP-стороны, ни отдельного "архивного"
  концепта, применимого к `courses`, не нашлось.

Никаких миграций — оба фильтра используют существующие колонки.

## Validation Results

- Новые тесты: `pytest tests/test_tsk559_active_blocked_filters.py -v` → 9/9 PASS.
- Полный прогон: `pytest -q` → **1622 passed, 11 skipped**, 0 failed (было
  1534/11 на момент последнего похожего изменения tsk-539 — рост числа
  тестов ожидаем, регрессий нет).
- Регресс без параметра подтверждён явными тестами
  (`test_tasks_by_course_without_param_is_unchanged`,
  `test_users_list_without_blocked_param_is_unchanged`) — поведение без
  `is_active`/`blocked` не отличается от того, что было до задачи.
- Пустой результат под фильтром — не ошибка, а `[]`/`200`
  (`test_tasks_by_course_is_active_empty_result_for_no_match`,
  `test_users_list_blocked_true_empty_result`).

## Risks and Follow-ups

- Курсы намеренно пропущены — если оператору позже понадобится реальная
  архивация курсов, это отдельная архитектурная задача (новая колонка +
  Alembic-миграция), не мелкая доделка.
- Cross-project contracts (`ContentBackbone/docs/cross-project/contracts/lms-api.md`)
  обновлены отдельным коммитом в ContentBackbone (новые query-параметры двух
  существующих эндпоинтов).
