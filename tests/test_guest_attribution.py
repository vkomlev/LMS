"""
Тесты гостевой сессии и атрибуции (Phase Y-1 + Y-5).

Y-1 endpoint'ы /embed/session* удалены в Y-5; этот файл теперь покрывает
unit-логику attribute_guest_session (registration-time tx). Полные
endpoint-тесты Y-5 — в test_y5_guest_endpoints.py.
"""
import pytest


@pytest.mark.asyncio
async def test_attribute_guest_session(db):
    """attribute_guest_session привязывает попытки к user_id."""
    from sqlalchemy import text
    from app.models.guest_session import GuestSession
    from app.models.guest_attempt import GuestAttempt
    from app.services.auth.guest_attribution_service import attribute_guest_session

    user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if user_id is None:
        pytest.skip("Нет пользователей в БД")

    gs = GuestSession(ip="127.0.0.1")
    db.add(gs)
    await db.flush()

    ga = GuestAttempt(
        guest_session_id=gs.id,
        task_id=None,
        answer_json={"x": 1},
    )
    db.add(ga)
    await db.flush()

    count = await attribute_guest_session(db, str(gs.id), user_id)
    await db.commit()

    assert count == 1
    await db.refresh(ga)
    assert ga.attributed_user_id == user_id
    assert ga.attributed_at is not None


@pytest.mark.asyncio
async def test_attribute_guest_session_idempotent(db):
    """Повторная атрибуция возвращает count=0 (уже привязанные попытки не обновляются)."""
    from sqlalchemy import text
    from app.models.guest_session import GuestSession
    from app.models.guest_attempt import GuestAttempt
    from app.services.auth.guest_attribution_service import attribute_guest_session

    user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if user_id is None:
        pytest.skip("Нет пользователей в БД")

    gs = GuestSession(ip="127.0.0.1")
    db.add(gs)
    await db.flush()

    ga = GuestAttempt(guest_session_id=gs.id, task_id=None, answer_json={})
    db.add(ga)
    await db.flush()

    await attribute_guest_session(db, str(gs.id), user_id)
    await db.commit()

    count2 = await attribute_guest_session(db, str(gs.id), user_id)
    await db.commit()

    assert count2 == 0
