"""tsk-1469: тесты POST /api/v1/poligon/login-by-email (вход ученика по email).

Покрывает:
- 200 happy path: сессионная cookie выдана (TTL=3600), пользователь с student-ролью входит
- email нормализуется (регистр/пробелы) — вход по варианту написания
- 401 без/с невалидным X-API-Key
- 404 на несуществующий email (без 500)
- 403 на пользователя БЕЗ student-роли (защита от входа под чужим боевым аккаунтом)
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text


def _api_key() -> str:
    raw = os.environ.get("VALID_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        pytest.skip("VALID_API_KEYS пуст в .env — пропускаем")
    return keys[0]


def _email() -> str:
    return f"tsk1469-{uuid.uuid4().hex[:16]}@example.com"


async def _cleanup(db, user_ids: list[int]):
    if not user_ids:
        return
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_modify"))
    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_modify"))
    await db.commit()


async def _make_user(db, *, with_student_role: bool) -> tuple[int, str]:
    email = _email()
    uid = (
        await db.execute(
            text("INSERT INTO users (email, full_name) VALUES (:e, :n) RETURNING id"),
            {"e": email, "n": "Ученик tsk-1469"},
        )
    ).scalar_one()
    if with_student_role:
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name = 'student'"))
        ).scalar_one()
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": uid, "r": role_id},
        )
    await db.commit()
    return uid, email


@pytest.mark.asyncio
async def test_login_happy_path_sets_cookie(db, client):
    uid, email = await _make_user(db, with_student_role=True)
    try:
        resp = await client.post(
            "/api/v1/poligon/login-by-email",
            json={"email": email},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == uid
        assert "Ученик tsk-1469" == body["full_name"]

        set_cookie = resp.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "Max-Age=3600" in set_cookie
        assert "HttpOnly" in set_cookie
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_login_email_normalized(db, client):
    uid, email = await _make_user(db, with_student_role=True)
    try:
        resp = await client.post(
            "/api/v1/poligon/login-by-email",
            json={"email": f"  {email.upper()} "},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == uid
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_login_missing_api_key_401(db, client):
    resp = await client.post(
        "/api/v1/poligon/login-by-email",
        json={"email": _email()},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_api_key_401(db, client):
    resp = await client.post(
        "/api/v1/poligon/login-by-email",
        json={"email": _email()},
        headers={"X-API-Key": "definitely-not-a-valid-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email_404(db, client):
    resp = await client.post(
        "/api/v1/poligon/login-by-email",
        json={"email": _email()},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404
    assert "не найден" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_without_student_role_403(db, client):
    """Пользователь без student-роли (потенциальный чужой аккаунт) не входит."""
    uid, email = await _make_user(db, with_student_role=False)
    try:
        resp = await client.post(
            "/api/v1/poligon/login-by-email",
            json={"email": email},
            headers={"X-API-Key": _api_key()},
        )
        assert resp.status_code == 403
        assert "не является учеником" in resp.json()["detail"]

        # и сессия не создана
        cnt = (
            await db.execute(
                text("SELECT COUNT(*) FROM user_session WHERE user_id = :u"), {"u": uid}
            )
        ).scalar()
        assert cnt == 0
    finally:
        await _cleanup(db, [uid])
