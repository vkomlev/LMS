# Review: Learning Engine этап 4 — Attempts integration

**Дата:** 2026-02-24

**Контекст:** Реализация этапа 4 по ТЗ: таймлимит из `tasks.time_limit_sec`, просрочка (time_expired, score=0), расширение ответов GET/finish новыми полями.

**Изменения:**
- **app/schemas/attempts.py**: в `AttemptRead` добавлено `time_expired`; в `AttemptWithResults` — опциональные `attempts_used`, `attempts_limit_effective`, `last_based_status`.
- **app/services/attempts_service.py**: добавлен `set_time_expired(attempt_id)`; `finish_attempt` принимает `time_expired=False` и при True проставляет оба поля.
- **app/api/v1/attempts.py**: таймлимит по `tasks.time_limit_sec` (и fallback на meta) в answers; при просрочке вызов `set_time_expired` и запись score=0; в finish проверка дедлайна по задачам попытки и вызов finish с time_expired; хелпер `_enrich_attempt_with_learning_fields` для attempts_used/limit/last_based_status по первой задаче; GET attempt и finish возвращают обогащённый ответ.
- **tests/test_attempts_integration_stage4.py**: проверка наличия полей на модели и в схеме.
- **docs/attempts-integration-stage4.md**: описание изменений API и правил таймлимита.
- **docs/learning-engine-v1-implementation-plan.md**: этап 4 отмечен как in progress с перечислением реализованного.

Полный diff (код): [2026-02-24-learning-engine-stage4-attempts-integration.diff](./2026-02-24-learning-engine-stage4-attempts-integration.diff)
