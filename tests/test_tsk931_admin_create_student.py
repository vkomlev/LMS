"""
tsk-931 — ручное создание ученика администратором (нет ВК, нет почты,
никогда не входил вообще).

Покрывает:
- POST /admin/students: гейт роли (403 не-admin), 201 + пустая карточка
  (full_name=NULL, роль student, audit-событие с заметкой).
- Заметка необязательна — создание проходит и без body.
- full_name остаётся невалидным для FullNameGate (SPW, tsk-223) — welcome-форма
  ФИО сработает при первом входе.
- Комбинированный поток: создать ученика → тем же admin выдать ему ссылку
  входа (tsk-930) → войти по ней.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import select, text

from app.models.audit_event import AuditEvent
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user(db, role: str | None) -> tuple[int, str]:
    suffix = random.randint(10**8, 10**10)
    u = Users(
        email=f"t931-{role or 'norole'}-{suffix}@example.com",
        password_hash=None,
        full_name=f"t931-{role or 'norole'}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
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
async def test_non_admin_cannot_create_student(db, client):
    _methodist_id, methodist_token = await _user(db, "methodist")

    resp = await client.post(
        "/api/v1/admin/students",
        headers=_auth(methodist_token),
        json={"note": "не должно пройти"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_admin_creates_student_with_empty_full_name(db, client):
    admin_id, admin_token = await _user(db, "admin")

    resp = await client.post(
        "/api/v1/admin/students",
        headers=_auth(admin_token),
        json={"note": "Кирилл Ф. — полная фамилия уточнится позже"},
    )
    assert resp.status_code == 201, resp.text
    student_id = resp.json()["id"]

    row = (
        await db.execute(select(Users).where(Users.id == student_id))
    ).scalar_one()
    assert row.full_name is None
    assert row.email is None

    roles = (
        await db.execute(
            text(
                "SELECT r.name FROM user_roles ur "
                "JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :id"
            ),
            {"id": student_id},
        )
    ).scalars().all()
    assert roles == ["student"]

    events = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "admin.student.created_manually",
                AuditEvent.user_id == student_id,
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].details["created_by_user_id"] == admin_id
    assert events[0].details["note"] == "Кирилл Ф. — полная фамилия уточнится позже"


@pytest.mark.asyncio
async def test_note_is_optional(db, client):
    _admin_id, admin_token = await _user(db, "admin")

    resp = await client.post(
        "/api/v1/admin/students",
        headers=_auth(admin_token),
        json={},
    )
    assert resp.status_code == 201, resp.text

    resp_no_body = await client.post(
        "/api/v1/admin/students",
        headers=_auth(admin_token),
    )
    assert resp_no_body.status_code == 201, resp_no_body.text


@pytest.mark.asyncio
async def test_full_flow_create_then_issue_login_link_and_login(db, client):
    """Комбинированный сценарий: создать ученика -> выдать ссылку -> войти."""
    _admin_id, admin_token = await _user(db, "admin")

    created = await client.post(
        "/api/v1/admin/students",
        headers=_auth(admin_token),
        json={"note": "e2e tsk-931"},
    )
    assert created.status_code == 201, created.text
    student_id = created.json()["id"]

    issued = await client.post(
        f"/api/v1/admin/students/{student_id}/issue-login-link",
        headers=_auth(admin_token),
    )
    assert issued.status_code == 201, issued.text
    token = issued.json()["url"].rsplit("token=", 1)[1]

    verify = await client.post(
        "/api/v1/auth/magic-link/verify",
        json={"token": token},
    )
    assert verify.status_code == 200, verify.text

    from app.services.auth.session_service import validate_session
    session = await validate_session(db, verify.json()["access_token"])
    assert session is not None
    assert session.user_id == student_id
