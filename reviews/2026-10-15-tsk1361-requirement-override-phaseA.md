# tsk-1361 фаза А — таблица персональной обязательности (тень)

**Цель.** Правила прощения нового (tsk-692/1261/1294) и сокращения программы (tsk-798) записывают отступления от `requirement_level` с причиной в `student_requirement_override`. Экраны и движок таблицу ещё не читают — поведение не меняется. План: `docs/specs/2026-10-15-plan-tsk1361-requirement-overrides.md` (одобрен оператором 15.10).

**Файлы.**
- миграция `2026_10_15_tsk1361_requirement_override.py`, модель `app/models/requirement_override.py`;
- `content_grace_service.compute_grace_reasons` — тот же расчёт, плюс код критерия; `compute_graced_items` выдаёт прежний набор;
- `requirement_override_service` — сверка правил, решения учителя, чтение;
- `requirement_override_sync_cron_service` + настройки + регистрация в `main.py`;
- `scripts/tsk1361_reconcile_overrides.py` — сверка таблицы с расчётом (только чтение);
- тесты `tests/test_tsk1361_requirement_override.py`.

**Валидация.**
- `pytest tests/test_tsk1361_requirement_override.py tests/test_tsk1261_grace_by_position.py tests/test_tsk692_content_grace.py` → `20 passed`;
- 11 файлов тестов прощения, объёма и нормы ДЗ → `181 passed in 91.41s`;
- `alembic heads` → `tsk1361_requirement_override (head)`; `alembic upgrade head` на dev прошёл;
- dev: проход сверки `{'students': 9, 'inserted': 861, 'revoked': 0, 'failed': 0}`, сверка `с расхождением 0`.

**Rollback.** Выключить `REQUIREMENT_OVERRIDE_SYNC_CRON_ENABLED=false` + рестарт; полностью — `alembic downgrade tsk994_final_check` (потребителей у таблицы нет).

Diff: `reviews/2026-10-15-tsk1361-requirement-override-phaseA.diff`.
