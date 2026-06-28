# Квиз SC_Qw/MC_Qw — ровно одна попытка (tsk-124)

**Дата:** 2026-06-28
**Ветка:** `feat/quiz-single-attempt`
**Связано:** tsk-122 (квиз-движок), ADR-0003

## Контекст

Квиз-вопросы (SC_Qw/MC_Qw) измеряют баллы по шкалам — у них нет «верно/неверно».
Раньше фронт показывал 3 попытки (default-лимит `DEFAULT_MAX_ATTEMPTS`). Повторные
попытки бессмысленны и могли бы задвоить накопление `scale_scores`. Требование:
для квиза всегда ровно ОДНА попытка; сервер — источник истины.

## Что найдено

- «3» берётся из `DEFAULT_MAX_ATTEMPTS = 3`. Лимит считается в **двух** местах:
  - `learning_engine_service.get_effective_attempt_limit` (override → `tasks.max_attempts` → 3);
  - SQL `_SYLLABUS_TASKS_SQL` в `me_service` (`/me/.../syllabus-states`).
  Оба отдают `attempts_limit_effective`, который читают фронты.
- Двойной учёт `scale_scores` уже защищён: `_accumulate_course_scales` берёт
  `DISTINCT ON (task_id)` последний результат. Подтверждено тестом tsk-122.
- `submit_attempt_answers` ранее не проверял лимит попыток вообще.

## Изменения

1. `app/services/learning_engine_service.py` — `get_effective_attempt_limit`:
   для типов `SC_Qw`/`MC_Qw` всегда возвращает `1` (приоритет 0 — выше override и
   `max_attempts`). Константа `QUIZ_MAX_ATTEMPTS = 1`.
2. `app/services/me_service.py` — `_SYLLABUS_TASKS_SQL`: `CASE` по
   `task_content->>'type'` → квиз даёт `attempts_limit_effective = 1`, остальные
   типы без изменений.
3. `app/api/v1/attempts.py` — `submit_attempt_answers`: жёсткий серверный запрет —
   если по квиз-задаче уже есть `task_result` в неотменённой попытке, повторный
   ответ отклоняется `409 Conflict`.
4. `tests/test_quiz_single_attempt_tsk124.py` — 7 тестов (см. ниже).

## Влияние на фронты (правок не требуется)

- **SPW** (`task/[external_uid]/page.tsx`): строка «Попыток: used / limit» читает
  `attempts_limit_effective` → покажет «1 / 1». «Попробуйте ещё раз» завязано на
  `is_correct === false`; у квиза `is_correct = null` → не показывается. После
  ответа — «К следующему шагу».
- **TG_LMS** (`student_attempts_base.py`): читает `attempts_limit_effective`,
  для квиза нейтральный итог «Ответ учтён». Подхватит лимит из контракта.

## Тесты

`tests/test_quiz_single_attempt_tsk124.py` (dev-БД, self-cleanup):
- лимит квиза = 1 при `max_attempts=3`;
- лимит квиза = 1 при персональном override=5;
- обычная SC-задача уважает `max_attempts` (регрессия);
- syllabus-SQL: квиз → 1, SC → default 3;
- один ответ на квиз → `compute_task_state = PASSED`, `attempts_limit_effective = 1`;
- повторный ответ на квиз → 409 (в той же и в новой попытке), остаётся 1 `task_result`.

**Прогон:** `24 passed` (7 новых + 11 + 6 регрессионных квиз), смежные движок/попытки/
syllabus `29 passed`.

## Риски / follow-ups

- **Отдельный дефект (не в scope):** в `me_service._compute_syllabus_task_status`
  квиз (`is_correct = null`) маппится в `pending_review` — в SPW-syllabus квиз
  висит «на проверке», хотя auto-check завершён. Нужна квиз-ветка статуса. Заведено
  отдельно.
- Пустой ответ на квиз (SC_Qw, `selected=[]`) даёт `score=0` → при лимите 1
  состояние `BLOCKED_LIMIT`. Для routing-квиза пропуск, вероятно, должен не блокировать —
  отдельное решение методиста.
