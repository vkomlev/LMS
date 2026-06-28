# Квиз SC_Qw/MC_Qw — серверный syllabus-статус `passed` (tsk-125)

**Дата:** 2026-06-28
**Ветка:** `fix/tsk-125-quiz-syllabus-passed`
**Связано:** tsk-122 (квиз-движок), tsk-124 (одна попытка), ADR-0003

## Контекст / дефект

`checking_service._check_quiz` пишет для квиза `is_correct=NULL` и `score=max_score`
(при ответе). Эндпоинт `GET /api/v1/me/courses/{course_id}/syllabus-states` через
`me_service._compute_syllabus_task_status` маппил `is_correct IS NULL` в
`pending_review` (legacy-ветка для pre-Y-6 SA_COM/TA). Поэтому отвеченный квиз
показывался «на проверке», хотя он авто-проверяемый и пройден. Расхождение с
`learning_engine_service.compute_task_state`, который для квиза даёт PASSED по
score-ratio. Ранее компенсировалось на фронте SPW (`normalizeQuizStatus`).

## Изменения

1. `app/services/me_service.py`:
   - `_SYLLABUS_TASKS_SQL` — добавлен `t.task_content->>'type' AS task_type` в SELECT.
   - `_compute_syllabus_task_status` — для квиз-типов (`QUIZ_TASK_TYPES`) при
     `is_correct IS NULL` статус считается по score-ratio с тем же
     `PASS_THRESHOLD_RATIO`, что и `compute_task_state`:
     - ratio ≥ порога → `passed`;
     - иначе `attempts_used >= limit` → `blocked_limit` (квиз = 1 попытка, tsk-124);
     - иначе → `failed`.
   - Не-квизовые `is_correct IS NULL` (legacy SA_COM/TA) остаются `pending_review`.
2. `tests/test_y62_syllabus_states.py` — 2 кейса:
   - отвеченный SC_Qw (score=max) → `passed`;
   - пустой ответ на квиз (score=0) при limit=1 → `blocked_limit`.

## Review-gate (12 измерений) — ПРИНЯТО

- **Корректность/паритет:** статус syllabus совпадает с `compute_task_state`
  (общий `PASS_THRESHOLD_RATIO`). Не-квизы не затронуты.
- **Контракт:** значения статусов прежние (`passed`/`failed`/`blocked_limit`/
  `pending_review`) — OpenAPI-схема не меняется, меняется только маппинг квиза.
- **Тесты:** live HTTP через эндпоинт (не mock-only). `37 passed`
  (syllabus + квиз-наборы).
- **Cross-project:** mirror lms-api.md + CHANGELOG обновлены.

## Остаток (отдельно, не блокирует)

- **SPW** (`lib/learning/use-course-syllabus.ts` → `normalizeQuizStatus`): клиентский
  костыль теперь избыточен (сервер уже отдаёт `passed`). Удаление — отдельной правкой
  в репозитории SPW; оставить безопасно (идемпотентно: normalize видит уже `passed`).

## Прогон

`python -m pytest tests/test_y62_syllabus_states.py tests/test_quiz_scales_tsk122.py
tests/test_quiz_single_attempt_tsk124.py -q` → **37 passed**.
