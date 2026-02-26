# Learning Engine V1, этап 3.5: аннулирование попытки

**Дата:** 2026-02-26

**Контекст:** Реализация ТЗ из `docs/tz-learning-engine-stage3-5-attempt-cancel.md`: endpoint отмены активной попытки, исключение отменённых из активного потока и статистики, идемпотентность, тесты и документация.

**Изменения:**
- Модель `Attempts`: поля `cancelled_at`, `cancel_reason`.
- Сервис `AttemptsService.cancel_attempt`: 404/409/200, идемпотентность.
- Схемы `AttemptCancelRequest`, `AttemptCancelResponse`; в `AttemptRead` добавлены `cancelled_at`, `cancel_reason`.
- API `POST /attempts/{attempt_id}/cancel` с опциональным body `reason`.
- Learning API: в start-or-get-attempt активная попытка — без `cancelled_at`.
- Статистика и last-attempt: в `task_results_service._last_attempts_flat` и в `learning_engine_service` везде добавлено условие `a.cancelled_at IS NULL` для учёта только незаменённых завершённых попыток.
- Тесты: `tests/test_attempt_cancel_stage35.py` (6 сценариев).
- Документация: `docs/api-reference.md`, `docs/assignments-and-results-api.md`.

Миграция `20260226_100000_attempts_cancel_stage35.py` уже существовала и была применена (`alembic upgrade head`).

**Начало diff:**

```diff
diff --git a/app/api/v1/attempts.py b/app/api/v1/attempts.py
index 27a33bb..a23a744 100644
--- a/app/api/v1/attempts.py
+++ b/app/api/v1/attempts.py
@@ -20,6 +20,8 @@ from app.schemas.attempts import (
     AttemptAnswersResponse,
     AttemptAnswerResult,
     AttemptFinishResponse,
+    AttemptCancelRequest,
+    AttemptCancelResponse,
 )
...
```

**Полный diff:** [2026-02-26-attempt-cancel-stage35.diff](2026-02-26-attempt-cancel-stage35.diff)
