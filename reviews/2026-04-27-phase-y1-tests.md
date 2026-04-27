# Review: Phase Y-1 — Test Suite

## Цель
Покрыть тестами весь auth-слой Phase Y-1 (B3 из review-gate). Устранить asyncpg/asyncio конфликт на Windows.

## Затронутые файлы
- `pytest.ini` — asyncio_mode=auto, asyncio_default_fixture_loop_scope=function
- `tests/conftest.py` — NullPool engine per test, dependency_overrides для client fixture
- `tests/test_migrations.py` — roundtrip downgrade/upgrade M3-M5
- `tests/test_auth_magic_link.py` — magic link send/verify/replay/expired
- `tests/test_auth_tg_init.py` — TG initData verify (unit + integration)
- `tests/test_auth_vk_callback.py` — VK callback invalid/missing fields
- `tests/test_session_lifecycle.py` — create/validate/revoke/refresh/revoke_all
- `tests/test_identity_linking.py` — find/upsert/normalize/idempotent
- `tests/test_guest_attribution.py` — guest session/attempt API + attribution
- `tests/test_idor_sweep.py` — parametrized 401/403 sweep + legacy api_key
- `app/api/v1/embed_api.py` — task_id сделан Optional (DB поддерживает NULL)

## Ключевые решения

### Windows asyncpg + asyncio PoolEventLoop fix
Глобальный `QueuePool` хранит asyncpg-соединения, привязанные к конкретному event loop. Когда pytest-asyncio создаёт новый loop для каждой test function, pooled соединения из предыдущего loop вызывают `RuntimeError: Future attached to a different loop`.

**Решение:** `NullPool` per test в conftest. Для `db` fixture — отдельный engine с NullPool. Для `client` fixture — `dependency_overrides[get_async_db]` заменяет зависимость на NullPool-сессию. Плюс `asyncio.WindowsSelectorEventLoopPolicy()` для совместимости asyncpg с Windows.

### _get_existing_user_id helper
Тесты, требующие FK на users, используют `SELECT MIN(id) FROM users` вместо hardcoded ID=1 (фактические ID в тестовой БД — 2, 3).

### db.refresh(ga) вместо db.expire + db.get
После bulk UPDATE через `update()` SQLAlchemy ORM-объект в identity map остаётся stale. `db.expire(ga)` также экспайрит PK, вызывая DetachedInstanceError при последующем lazy load в async сессии. `await db.refresh(ga)` явно перечитывает объект из БД.

## Результаты валидации

```
47 passed, 7 warnings in 33.16s  (функциональные тесты)
3 passed, 11 warnings in 11.00s  (migration roundtrip)
```

## Rollback note
Тесты не влияют на production code (кроме embed_api.py: task_id Optional).
Откат: `git checkout tests/ app/api/v1/embed_api.py pytest.ini`
