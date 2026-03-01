# Review: Stage 3.9 — Правки по расширенному ревью (P2/P3)

**Дата:** 2026-03-01  
**Контекст:** Закрытие замечаний по идемпотентности (документ, память, negative-cache), наблюдаемость, детерминированный тест release help.

## Внесённые изменения

### [P1] Документирование ограничения in-memory идемпотентности
- В `app/services/teacher_queue_service.py`: в модульном docstring указано, что кэш локальнен процессу и при нескольких воркерах возможен повторный claim; дана ссылка на ТЗ и рекомендация (Redis/БД).
- В `docs/tz-learning-engine-stage3-9-teacher-next-modes.md`: в разделе Risks and Rollback добавлен подпункт «Ограничение: идемпотентность claim-next» с рекомендацией переноса в общий store.

### [P2] Очистка просроченных записей кэша
- Добавлена функция `_prune_idempotency_cache(now)`: удаляет записи с `cache_until < now`. Вызывается при каждом входе под `_idempotency_lock` (lookup и store), чтобы ограничить рост памяти.

### [P2] Negative-cache на empty
- `_IDEM_EMPTY_TTL_SEC` уменьшен с 60 до 30 сек.
- В комментарии к константе указано: «при empty кэшируем на короткий TTL; новые кейсы могут не появиться в ответе до истечения TTL».

### [P3] Наблюдаемость cache hit/miss/size
- Счётчики `_idempotency_cache_hits`, `_idempotency_cache_misses` и их увеличение при hit/miss.
- Логирование при cache hit: `logger.info("claim_idempotent_hit queue=help|review key_prefix=... cache_size=...")`.
- Функция `get_idempotency_cache_stats()` возвращает `idempotency_cache_size`, `idempotency_cache_hits`, `idempotency_cache_misses` для метрик/логов.

### [P3] Детерминированный тест release help 409
- В `test_release_help_wrong_token_409`: при `empty` вызывается хелпер `_seed_one_open_help_request(teacher_id)`, создающий одну открытую заявку (INSERT в `help_requests` с `assigned_teacher_id=teacher_id`), затем повторный claim и проверка release с неверным токеном → 409.
- Тест больше не зависит от наличия открытых заявок в БД и даёт стабильный PASS/FAIL.

## Валидация
- `python tests/test_teacher_next_modes_stage39.py` — все тесты PASS, в т.ч. release help (с seed при необходимости).

Полный diff: [2026-03-01-stage39-debugger-p2-p3-fixes.diff](2026-03-01-stage39-debugger-p2-p3-fixes.diff).
