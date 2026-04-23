# Review: Stage 3.9 — Правки по вердикту FastAPI Debugger

**Дата:** 2026-03-01  
**Контекст:** Закрытие P1/P2 замечаний ревью: идемпотентность claim, timezone в manual-check, тесты 409 и идемпотентности, фильтр by-pending-review.

## Внесённые исправления

### [P1] Идемпотентность claim по `idempotency_key`
- **Что сделано:** В `teacher_queue_service` добавлен in-memory кэш по ключу `(teacher_id, idempotency_key, "help"|"review")`. При повторном запросе с тем же ключом возвращается тот же `(item, lock_token, lock_expires_at)` без повторного claim в БД.
- **Файлы:** `app/services/teacher_queue_service.py`, вызовы из `app/api/v1/teacher_help_requests.py` и `app/api/v1/teacher_reviews.py` передают `idempotency_key`.

### [P1] Проверка lock в manual-check и naive/aware datetime
- **Что сделано:** Перед сравнением `review_claim_expires_at < now` значение нормализуется: если `tzinfo is None`, задаётся `timezone.utc`. Исключается 500 при naive datetime из БД.
- **Файл:** `app/api/v1/task_results_extra.py`.

### [P2] Тесты конфликтов и идемпотентности
- **Что сделано:** В `tests/test_teacher_next_modes_stage39.py` добавлены:
  - `test_claim_next_idempotency_same_response` — двойной POST с одним `idempotency_key` возвращает тот же `lock_token` и `item`;
  - `test_release_help_wrong_token_409` — release с неверным токеном → 409 (SKIP при отсутствии открытых заявок);
  - `test_release_review_wrong_token_409` — release review с неверным токеном → 409;
  - `test_manual_check_wrong_lock_token_409` — manual-check с неверным `lock_token` → 409.

### [P2] by-pending-review исключает захваченные по TTL
- **Что сделано:** В `get_pending_review_results` добавлено условие: `review_claim_expires_at IS NULL OR review_claim_expires_at < now`, чтобы в список не попадали результаты, захваченные по claim и ещё не просроченные.
- **Файл:** `app/api/v1/task_results_extra.py`.

## Валидация
- `python tests/test_teacher_next_modes_stage39.py` — все тесты PASS (один SKIP при отсутствии открытых help-заявок).

Полный diff: [2026-03-01-stage39-debugger-fixes.diff](2026-03-01-stage39-debugger-fixes.diff).
