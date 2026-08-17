"""tsk-235: окно благодати на ротацию refresh-токена (гонка вкладок SPW).

Две вкладки SPW делят одну refresh-cookie. Без окна благодати конкурентный
refresh тем же (уже отозванным ротацией) токеном получал 401 ("Не удалось
сохранить"), хотя первая вкладка уже успешно обновилась. Покрывает:

- Повтор в течение окна благодати → та же пара токенов преемника, БЕЗ
  создания ещё одной сессии (идемпотентность, цепочка не размножается).
- Повтор ПОСЛЕ окна благодати → 401 + отзыв цепочки сессий, которой принадлежит
  токен (детект кражи/replay токена сохраняется; радиус сужен в tsk-604 —
  см. test_session_refresh_replay_scope_tsk604.py).
- Токен отозван НЕ через ротацию (нет replaced_by_session_id) → обычный 401,
  поведение не меняется, цепочка не трогается (ложного срабатывания нет).
- Без Redis (redis=None) — окно благодати недоступно, но обычная ротация
  по-прежнему работает (регресс к pre-fix поведению, не отказ).
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text


async def _get_existing_user_id(db) -> int:
    uid = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if uid is None:
        pytest.skip("Нет пользователей в БД")
    return uid


@pytest_asyncio.fixture()
async def redis_client():
    """Реальный dev-Redis (по образцу test_y5_guest_endpoints.py), с уборкой ключей."""
    import redis.asyncio as aioredis

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis недоступен локально — grace-window тесты пропущены")
    try:
        async for key in client.scan_iter(match="session_refresh_grace:*", count=200):
            await client.delete(key)
        yield client
    finally:
        async for key in client.scan_iter(match="session_refresh_grace:*", count=200):
            await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_refresh_within_window_returns_same_pair(db, redis_client):
    """Второй запрос с тем же (уже отозванным) refresh_token в окне благодати
    получает ТУ ЖЕ пару токенов, а не 401 и не создаёт третью сессию."""
    from app.services.auth.session_service import create_session, refresh_session

    user_id = await _get_existing_user_id(db)
    _, refresh_token, old_session = await create_session(db, user_id=user_id)
    await db.commit()

    first = await refresh_session(db, refresh_token, redis_client)
    assert first is not None
    first_access, first_refresh, first_session = first

    # "Вторая вкладка": тот же старый refresh_token, отправленный конкурентно.
    second = await refresh_session(db, refresh_token, redis_client)
    assert second is not None, "конкурентный повтор в окне благодати не должен давать 401"
    second_access, second_refresh, second_session = second

    assert second_access == first_access
    assert second_refresh == first_refresh
    assert second_session.id == first_session.id

    # Цепочка не размножилась: у старой сессии ровно один преемник.
    await db.refresh(old_session)
    assert old_session.replaced_by_session_id == first_session.id


@pytest.mark.asyncio
async def test_replay_after_grace_window_revokes_chain(db, redis_client):
    """Повтор отозванного токена ПОСЛЕ окна благодати — подозрение на кражу:
    401 + отзыв цепочки этого токена (включая преемника)."""
    from app.services.auth.session_service import (
        _REFRESH_GRACE_WINDOW_SECONDS,
        _now,
        create_session,
        refresh_session,
        validate_session,
    )

    user_id = await _get_existing_user_id(db)
    _, refresh_token, old_session = await create_session(db, user_id=user_id)
    await db.commit()

    result = await refresh_session(db, refresh_token, redis_client)
    assert result is not None
    successor_access, _successor_refresh, successor_session = result

    # Симулируем, что окно благодати истекло (без реального ожидания).
    old_session.revoked_at = _now() - timedelta(seconds=_REFRESH_GRACE_WINDOW_SECONDS + 1)
    await db.flush()

    replay = await refresh_session(db, refresh_token, redis_client)
    assert replay is None

    # Цепочка отозвана целиком: даже успевший выпуститься преемник теперь мёртв.
    assert await validate_session(db, successor_access) is None


@pytest.mark.asyncio
async def test_revoked_without_successor_is_unchanged_401(db, redis_client):
    """Токен отозван НЕ через ротацию (logout) — обычный 401, цепочка не трогается."""
    from app.services.auth.session_service import (
        create_session,
        refresh_session,
        revoke_session,
        validate_session,
    )

    user_id = await _get_existing_user_id(db)
    other_access, _, other_session = await create_session(db, user_id=user_id)
    access_token, refresh_token, session_obj = await create_session(db, user_id=user_id)
    await db.commit()

    await revoke_session(db, session_obj.id)  # логаут, не ротация — replaced_by = NULL
    await db.commit()

    result = await refresh_session(db, refresh_token, redis_client)
    assert result is None

    # Не признак кражи — другая активная сессия того же пользователя не тронута.
    assert await validate_session(db, other_access) is not None


@pytest.mark.asyncio
async def test_refresh_without_redis_still_rotates_normally(db):
    """Без Redis (redis=None) окно благодати недоступно, но обычная (не гоночная)
    ротация продолжает работать как раньше — не отказ, а деградация фичи."""
    from app.services.auth.session_service import create_session, refresh_session, validate_session

    user_id = await _get_existing_user_id(db)
    access_token, refresh_token, _ = await create_session(db, user_id=user_id)
    await db.commit()

    result = await refresh_session(db, refresh_token, None)
    assert result is not None
    new_access, _new_refresh, _new_session = result
    await db.commit()

    assert new_access != access_token
    assert await validate_session(db, new_access) is not None
    assert await validate_session(db, access_token) is None
