"""Integration HTTP-тесты GET /api/v1/me — поле `roles` (tsk-298, Фаза 0).

Покрывает:
- ученик без teacher-роли → roles == ['student'];
- преподаватель → roles содержит 'teacher';
- мульти-роль teacher+student → roles == ['student', 'teacher'] (отсортировано);
- аддитивность: поле присутствует и является списком строк.

Образец подъёма user+session — как в test_me_profile_update.py.
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user_with_roles(db, roles: list[str]) -> tuple[int, str]:
    """Создать user + email-identity + указанные роли + session.

    :return: (user_id, access_token).
    """
    email = f"tsk298_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=None, tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    for role_name in roles:
        role_id = (
            await db.execute(
                text("SELECT id FROM roles WHERE name = :n"), {"n": role_name}
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "r": role_id},
        )
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _cleanup(db, user_id: int) -> None:
    """Cleanup без DELETE FROM users (FK SET NULL на audit_event → триггер)."""
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_me_roles_student_only(db, client):
    """Ученик без teacher-роли: roles == ['student']."""
    user_id, token = await _setup_user_with_roles(db, ["student"])
    try:
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["roles"] == ["student"]
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_roles_teacher(db, client):
    """Преподаватель: roles содержит 'teacher'."""
    user_id, token = await _setup_user_with_roles(db, ["teacher"])
    try:
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["roles"] == ["teacher"]
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_roles_multi_teacher_student(db, client):
    """Мульти-роль: roles == ['student', 'teacher'] (отсортировано по алфавиту)."""
    user_id, token = await _setup_user_with_roles(db, ["teacher", "student"])
    try:
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["roles"] == ["student", "teacher"]
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_me_roles_field_is_list(db, client):
    """Аддитивность: поле `roles` всегда присутствует и является списком строк."""
    user_id, token = await _setup_user_with_roles(db, ["student"])
    try:
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "roles" in body
        assert isinstance(body["roles"], list)
        assert all(isinstance(r, str) for r in body["roles"])
    finally:
        await _cleanup(db, user_id)
