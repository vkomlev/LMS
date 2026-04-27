"""
Тесты жизненного цикла user_session.

Покрывает:
- create_session → validate_session → revoke_session
- refresh_session: старая сессия отзывается, новая создаётся
- Невалидный access_token → None
- Несуществующий токен → None
"""
import os
import pytest


async def _get_existing_user_id(db) -> int:
    from sqlalchemy import text
    uid = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if uid is None:
        pytest.skip("Нет пользователей в БД")
    return uid


@pytest.mark.asyncio
async def test_create_and_validate_session(db):
    """Созданная сессия валидируется по access_token."""
    from app.services.auth.session_service import create_session, validate_session

    user_id = await _get_existing_user_id(db)
    access_token, refresh_token, session_obj = await create_session(db, user_id=user_id)
    await db.commit()

    result = await validate_session(db, access_token)
    assert result is not None
    assert result.user_id == user_id


@pytest.mark.asyncio
async def test_validate_invalid_token(db):
    """Невалидный hex токен → None."""
    from app.services.auth.session_service import validate_session

    assert await validate_session(db, "not-a-hex") is None


@pytest.mark.asyncio
async def test_validate_nonexistent_token(db):
    """Несуществующий токен → None."""
    from app.services.auth.session_service import validate_session

    fake = os.urandom(32).hex()
    assert await validate_session(db, fake) is None


@pytest.mark.asyncio
async def test_revoke_session(db):
    """После revoke_session validate возвращает None."""
    from app.services.auth.session_service import create_session, revoke_session, validate_session

    user_id = await _get_existing_user_id(db)
    access_token, _, session_obj = await create_session(db, user_id=user_id)
    await db.commit()

    await revoke_session(db, session_obj.id)
    await db.commit()

    assert await validate_session(db, access_token) is None


@pytest.mark.asyncio
async def test_refresh_session(db):
    """refresh_session отзывает старую и создаёт новую пару токенов."""
    from app.services.auth.session_service import create_session, refresh_session, validate_session

    user_id = await _get_existing_user_id(db)
    access_token, refresh_token, old_session = await create_session(db, user_id=user_id)
    await db.commit()

    result = await refresh_session(db, refresh_token)
    assert result is not None
    new_access, new_refresh, new_session = result
    await db.commit()

    assert new_access != access_token
    assert await validate_session(db, access_token) is None
    assert await validate_session(db, new_access) is not None


@pytest.mark.asyncio
async def test_refresh_invalid_token(db):
    """Несуществующий refresh_token → None."""
    from app.services.auth.session_service import refresh_session

    fake = os.urandom(32).hex()
    assert await refresh_session(db, fake) is None


@pytest.mark.asyncio
async def test_revoke_all_sessions(db):
    """revoke_all_sessions отзывает все активные сессии пользователя."""
    from app.services.auth.session_service import create_session, revoke_all_sessions, validate_session

    user_id = await _get_existing_user_id(db)
    t1, _, _ = await create_session(db, user_id=user_id)
    t2, _, _ = await create_session(db, user_id=user_id)
    await db.commit()

    await revoke_all_sessions(db, user_id=user_id)
    await db.commit()

    assert await validate_session(db, t1) is None
    assert await validate_session(db, t2) is None
