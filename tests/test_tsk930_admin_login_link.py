"""
tsk-930: admin-выдача ссылки входа ученику вручную (нет ВК, не приходит почта).

Покрывает:
- POST /admin/students/{id}/issue-login-link: гейт роли (403 не-admin),
  404 несуществующего ученика, 201 + корректная выдача (TTL=24ч, аудит).
- Ученик БЕЗ email/identity вообще — ключевой случай задачи.
- POST /auth/magic-link/verify консьюмит admin-выданный токен → сессия
  ИМЕННО ученика (не админа), одноразово (replay → 401).
- TTL=24ч admin-ссылки отличается от TTL=15мин обычного письма — независимые
  константы, не общая.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.config import Settings
from app.models.audit_event import AuditEvent
from app.models.users import Users
from app.services.auth import identity_link_service, magic_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _user(db, role: str | None, *, with_email: bool = True) -> tuple[int, str]:
    """Создать пользователя; при with_email=False — БЕЗ users.email и identity_link
    вовсе (ровно случай ученика без ВК и без почты из tsk-930)."""
    suffix = random.randint(10**8, 10**10)
    u = Users(
        email=f"t930-{role or 'norole'}-{suffix}@example.com" if with_email else None,
        password_hash=None,
        full_name=f"t930-{role or 'norole'}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    if with_email:
        await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_non_admin_cannot_issue_link(db, client):
    """methodist получает 403 — эта ручка строго admin, в отличие от tsk-498."""
    methodist_id, methodist_token = await _user(db, "methodist")
    student_id, _ = await _user(db, "student", with_email=False)

    resp = await client.post(
        f"/api/v1/admin/students/{student_id}/issue-login-link",
        headers=_auth(methodist_token),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_issue_link_for_nonexistent_student_404(db, client):
    admin_id, admin_token = await _user(db, "admin")
    resp = await client.post(
        "/api/v1/admin/students/999999999/issue-login-link",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_admin_issues_link_for_student_without_email(db, client):
    """Ключевой случай задачи: у ученика нет ни email, ни identity_link вовсе."""
    admin_id, admin_token = await _user(db, "admin")
    student_id, _ = await _user(db, "student", with_email=False)

    before = datetime.now(timezone.utc)
    resp = await client.post(
        f"/api/v1/admin/students/{student_id}/issue-login-link",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["student_id"] == student_id
    assert body["issued_by_user_id"] == admin_id
    assert "/auth/magic-link/consume?token=" in body["url"]

    expires_at = datetime.fromisoformat(body["expires_at"])
    delta = expires_at - before
    # TTL=24ч (не 15 мин обычного письма) — допуск на время выполнения теста.
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)

    # Audit-след: кто/кому/когда сгенерировал.
    events = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "auth.admin.login_link_issued",
                AuditEvent.user_id == student_id,
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].details["issued_by_user_id"] == admin_id
    assert events[0].details["ttl_hours"] == 24


@pytest.mark.asyncio
async def test_generated_link_logs_in_as_the_student_not_admin(db, client):
    """Verify той же ссылки — сессия ИМЕННО ученика, полные права, не админа."""
    admin_id, admin_token = await _user(db, "admin")
    student_id, _ = await _user(db, "student", with_email=False)

    issue = await client.post(
        f"/api/v1/admin/students/{student_id}/issue-login-link",
        headers=_auth(admin_token),
    )
    assert issue.status_code == 201, issue.text
    url = issue.json()["url"]
    token = url.rsplit("token=", 1)[1]

    verify = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token},
    )
    assert verify.status_code == 200, verify.text
    access_token = verify.json()["access_token"]

    from app.services.auth.session_service import validate_session
    session = await validate_session(db, access_token)
    assert session is not None
    assert session.user_id == student_id


@pytest.mark.asyncio
async def test_admin_issued_link_is_single_use(db, client):
    admin_id, admin_token = await _user(db, "admin")
    student_id, _ = await _user(db, "student", with_email=False)

    issue = await client.post(
        f"/api/v1/admin/students/{student_id}/issue-login-link",
        headers=_auth(admin_token),
    )
    token = issue.json()["url"].rsplit("token=", 1)[1]

    first = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert first.status_code == 200, first.text

    replay = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert replay.status_code == 401, replay.text


@pytest.mark.asyncio
async def test_admin_issued_ttl_is_24h_independent_of_15m_email_ttl(db):
    """TTL admin-выдачи (24ч) и TTL обычного письма (15 мин) — раздельные константы."""
    admin_id, _ = await _user(db, "admin")
    student_id, _ = await _user(db, "student", with_email=False)

    before = datetime.now(timezone.utc)
    link, _raw = await magic_link_service.create_magic_link_for_user(
        db, user_id=student_id, issued_by_user_id=admin_id,
    )
    await db.commit()
    assert timedelta(hours=23, minutes=55) < (link.expires_at - before) < timedelta(hours=24, minutes=5)

    email_token = await magic_link_service.create_magic_link(db, "t930-plain@example.com")
    await db.commit()
    from app.models.magic_link import MagicLink
    import hashlib
    plain = (
        await db.execute(
            select(MagicLink).where(
                MagicLink.token_hash == hashlib.sha256(bytes.fromhex(email_token)).digest()
            )
        )
    ).scalar_one()
    before2 = datetime.now(timezone.utc)
    assert timedelta(minutes=10) < (plain.expires_at - before2) < timedelta(minutes=16)
