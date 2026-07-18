"""Тесты централизованного роль-гейта require_role / require_teacher (tsk-298).

Юнит (dependency вызывается напрямую):
- teacher → пропуск (возвращает CurrentUser);
- не-teacher (student) → 403;
- сервисный токен → bypass (пропуск без проверки роли).

Интеграция (require_role e2e через методист-эндпоинт, чьё поведение НЕ должно
измениться после централизации):
- GET /api/v1/methodist/escalations/pending: 403 для не-методиста, 200 для
  методиста, bypass по X-API-Key.
"""
import random

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.deps import require_role, require_teacher
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _setup_user_with_roles(db, roles: list[str]) -> tuple[int, str]:
    """Создать user + email-identity + роли + session. Возврат (user_id, token)."""
    email = f"tsk298r_{random.randint(10**8, 10**10)}@example.com"
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
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── Юнит: require_teacher ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_require_teacher_allows_teacher(db):
    """Пользователь с ролью teacher проходит гейт."""
    user_id, _ = await _setup_user_with_roles(db, ["teacher"])
    try:
        result = await require_teacher(
            current_user=CurrentUser(id=user_id, is_service=False), db=db
        )
        assert result.id == user_id
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_require_teacher_rejects_non_teacher(db):
    """Пользователь без роли teacher (только student) → 403."""
    user_id, _ = await _setup_user_with_roles(db, ["student"])
    try:
        with pytest.raises(HTTPException) as exc:
            await require_teacher(
                current_user=CurrentUser(id=user_id, is_service=False), db=db
            )
        assert exc.value.status_code == 403
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_require_teacher_service_bypass(db):
    """Сервисный токен — bypass (роль не проверяется)."""
    result = await require_teacher(
        current_user=CurrentUser(id=0, is_service=True), db=db
    )
    assert result.is_service is True


@pytest.mark.asyncio
async def test_require_role_multi_names(db):
    """require_role с несколькими ролями: достаточно одной из них."""
    dep = require_role("teacher", "admin")
    user_id, _ = await _setup_user_with_roles(db, ["teacher"])
    try:
        result = await dep(
            current_user=CurrentUser(id=user_id, is_service=False), db=db
        )
        assert result.id == user_id
    finally:
        await _cleanup(db, user_id)


# ── Интеграция: require_role e2e через методист-эндпоинт ─────────────────────

@pytest.mark.asyncio
async def test_methodist_escalations_forbidden_for_non_methodist(db, client):
    """Не-методист (student) получает 403 на /methodist/escalations/pending."""
    user_id, token = await _setup_user_with_roles(db, ["student"])
    try:
        resp = await client.get(
            "/api/v1/methodist/escalations/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_methodist_escalations_ok_for_methodist(db, client):
    """Методист получает 200 (список, возможно пустой)."""
    user_id, token = await _setup_user_with_roles(db, ["methodist"])
    try:
        resp = await client.get(
            "/api/v1/methodist/escalations/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_methodist_escalations_service_key_bypass(client):
    """Сервисный X-API-Key — bypass роль-проверки, 200."""
    api_key = next(iter(_settings.valid_api_keys))
    resp = await client.get(
        "/api/v1/methodist/escalations/pending",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()
