"""tsk-1407: привязка лида к ученику копирует метки заявки в signup_attribution.

Дефект: метка источника из `leads.attribution` (бот записи с лендинга, форма
сайта) доходила до заявки, но не до пользователя — конверсия заявки → ученик
(`lead_service.link_student`) копирование не делала. Покрывает:
- копия attribution (+guest_session_id) в пустое `users.signup_attribution`;
- закрытие гостевой сессии заявки за учеником (`guest_session.attributed_user_id`);
- первое касание не перезаписывается;
- заявка без меток привязывается как раньше, поле остаётся пустым.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.models.guest_session import GuestSession
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk1407link"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}-{random.randint(10**6, 10**7)}",
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
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _source_id(db, code: str) -> int:
    return (
        await db.execute(text("SELECT id FROM lead_source WHERE code = :c"), {"c": code})
    ).scalar()


async def _mk_lead(db, client, token: str, *, with_session: bool) -> tuple[int, uuid.UUID | None]:
    """Создать заявку website с метками, как их пишет бот записи (8cb4f67)."""
    website_id = await _source_id(db, "website")
    resp = await client.post(
        "/api/v1/marketer/leads",
        json={"source_id": website_id, "contact": "@lead"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    lead_id = resp.json()["id"]

    gs_id: uuid.UUID | None = None
    if with_session:
        gs = GuestSession(
            attribution={"utm_source": "victor-komlev.ru", "utm_campaign": "landing-ai"},
        )
        db.add(gs)
        await db.flush()
        gs_id = gs.id

    await db.execute(
        text(
            "UPDATE leads SET attribution = :a, guest_session_id = :g WHERE id = :id"
        ),
        {
            "a": '{"utm_source": "victor-komlev.ru", "utm_campaign": "landing-ai"}',
            "g": str(gs_id) if gs_id else None,
            "id": lead_id,
        },
    )
    await db.commit()
    return lead_id, gs_id


async def _link(client, token: str, lead_id: int, student_id: int):
    return await client.post(
        f"/api/v1/marketer/leads/{lead_id}/link",
        json={"student_id": student_id},
        headers=_auth(token),
    )


async def test_link_copies_attribution_to_student(db, client):
    _, token = await _new_user(db, role="marketer", name="mkt")
    student_id, _ = await _new_user(db, role="student", name="stu")
    lead_id, gs_id = await _mk_lead(db, client, token, with_session=True)

    resp = await _link(client, token, lead_id, student_id)
    assert resp.status_code == 200, resp.text

    row = (
        await db.execute(
            text("SELECT signup_attribution FROM users WHERE id = :id"), {"id": student_id}
        )
    ).one()
    attribution = row[0]
    assert attribution is not None
    assert attribution["utm_source"] == "victor-komlev.ru"
    assert attribution["utm_campaign"] == "landing-ai"
    assert attribution["guest_session_id"] == str(gs_id)

    attributed = (
        await db.execute(
            text("SELECT attributed_user_id FROM guest_session WHERE id = :id"),
            {"id": str(gs_id)},
        )
    ).scalar()
    assert attributed == student_id


async def test_link_does_not_overwrite_existing_attribution(db, client):
    _, token = await _new_user(db, role="marketer", name="mkt2")
    student_id, _ = await _new_user(db, role="student", name="stu2")
    lead_id, _ = await _mk_lead(db, client, token, with_session=False)

    await db.execute(
        text("UPDATE users SET signup_attribution = :a WHERE id = :id"),
        {"a": '{"utm_source": "avito"}', "id": student_id},
    )
    await db.commit()

    resp = await _link(client, token, lead_id, student_id)
    assert resp.status_code == 200, resp.text

    attribution = (
        await db.execute(
            text("SELECT signup_attribution FROM users WHERE id = :id"), {"id": student_id}
        )
    ).scalar()
    assert attribution == {"utm_source": "avito"}


async def test_link_without_attribution_leaves_none(db, client):
    _, token = await _new_user(db, role="marketer", name="mkt3")
    student_id, _ = await _new_user(db, role="student", name="stu3")
    website_id = await _source_id(db, "website")
    resp = await client.post(
        "/api/v1/marketer/leads",
        json={"source_id": website_id, "contact": "@plain"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text

    linked = await _link(client, token, resp.json()["id"], student_id)
    assert linked.status_code == 200, linked.text

    attribution = (
        await db.execute(
            text("SELECT signup_attribution FROM users WHERE id = :id"), {"id": student_id}
        )
    ).scalar()
    assert attribution is None
