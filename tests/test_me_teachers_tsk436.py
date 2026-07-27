"""tsk-436 (follow-up) — GET /me/teachers: cookie-авторизованная обёртка над
GET /users/{id}/teachers (тот эндпоинт защищён сервисным API-ключом для
ТГ-ботов, не вызывается из браузера). Нужен "плавающему" ученику без
закреплённого слота, чтобы узнать, к какому преподавателю записаться перед
ad-hoc бронированием (см. tsk-021).
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth.session_service import create_session


async def _create_user(db, *, full_name: str, role: str | None = None) -> int:
    user = Users(
        email=f"tsk436me_{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=full_name, tg_id=None,
    )
    db.add(user)
    await db.flush()
    if role:
        role_row = (
            await db.execute(text("SELECT id FROM roles WHERE name = :n"), {"n": role})
        ).fetchone()
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) ON CONFLICT DO NOTHING"),
            {"u": user.id, "r": role_row[0]},
        )
    await db.commit()
    return user.id


@pytest.mark.asyncio
async def test_me_teachers_requires_auth(client):
    resp = await client.get("/api/v1/me/teachers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_teachers_returns_linked_teachers(db, client):
    teacher_id = await _create_user(db, full_name="Тест Препод tsk436", role="teacher")
    student_id = await _create_user(db, full_name="Тест Ученик tsk436", role="student")
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/me/teachers", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    ids = [t["id"] for t in resp.json()]
    assert ids == [teacher_id]

    await db.execute(
        text("DELETE FROM student_teacher_links WHERE student_id = :s"), {"s": student_id},
    )
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": [teacher_id, student_id]})
    await db.commit()


@pytest.mark.asyncio
async def test_me_teachers_empty_for_floating_student(db, client):
    student_id = await _create_user(db, full_name="Плавающий Ученик tsk436", role="student")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/me/teachers", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": student_id})
    await db.commit()
