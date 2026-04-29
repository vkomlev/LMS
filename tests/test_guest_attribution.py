"""
Тесты гостевой сессии и атрибуции.

Покрывает:
- POST /embed/session → 201 + guest_session_id
- POST /embed/session/{id}/attempts → 201
- attribute_guest_session: guest_attempt привязывается к user_id
- Повторная атрибуция не удваивает привязку
"""
import pytest


@pytest.mark.asyncio
async def test_create_guest_session(client):
    """POST /embed/session создаёт гостевую сессию."""
    resp = await client.post("/api/v1/embed/session")
    assert resp.status_code == 201
    data = resp.json()
    assert "guest_session_id" in data
    assert len(data["guest_session_id"]) == 36


@pytest.mark.asyncio
async def test_create_guest_attempt(client):
    """POST /embed/session/{id}/attempts создаёт попытку гостя."""
    session_resp = await client.post("/api/v1/embed/session")
    session_id = session_resp.json()["guest_session_id"]

    attempt_resp = await client.post(
        f"/api/v1/embed/session/{session_id}/attempts",
        json={"answer_json": {"answer": "42"}, "is_correct": True},
    )
    assert attempt_resp.status_code == 201
    assert "attempt_id" in attempt_resp.json()


@pytest.mark.asyncio
async def test_attribute_guest_session(db):
    """attribute_guest_session привязывает попытки к user_id."""
    from sqlalchemy import text, select
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
