"""tsk-1171 — лента новых регистраций для бота админа.

Покрывает: гейт роли; первый запуск без after_id отдаёт только курсор;
способ регистрации из аудита; слитые и тестовые учётки пропускаются, но
курсор через них сдвигается; повторный запрос с курсором не повторяет учётки;
метки квиза из гостевой сессии.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import audit_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _staff(db, role: str) -> str:
    suffix = random.randint(10**8, 10**10)
    u = Users(email=f"t1171-{role}-{suffix}@example.com", full_name=f"t1171-{role}")
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
        ),
        {"u": u.id, "r": role},
    )
    await db.commit()
    return token


async def _registered(db, *, name: str | None, event: str, plan: str | None = None) -> int:
    u = Users(full_name=name, category="school_student", school_grade=9)
    db.add(u)
    await db.flush()
    await audit_service.log_event(db, event, user_id=u.id, details={})
    if plan is not None:
        await db.execute(
            text(
                "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
                "SELECT :u, p.id, current_date FROM subscription_plan p WHERE p.code = :c"
            ),
            {"u": u.id, "c": plan},
        )
    await db.commit()
    return u.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_non_admin_forbidden(db, client):
    token = await _staff(db, "methodist")
    resp = await client.get("/api/v1/admin/new-users", headers=_auth(token))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_first_run_returns_only_cursor(db, client):
    token = await _staff(db, "admin")
    resp = await client.get(
        "/api/v1/admin/new-users", params={"min_age_sec": 0}, headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["cursor"] > 0


@pytest.mark.asyncio
async def test_feed_filters_and_is_idempotent(db, client):
    token = await _staff(db, "admin")
    start = (await db.execute(text("SELECT MAX(id) FROM users"))).scalar_one()

    vk_id = await _registered(db, name="Иван Петров", event="user.registered.via_vk", plan="base")
    anon_id = await _registered(db, name=None, event="admin.student.created_manually")
    test_id = await _registered(db, name="Тестовый", event="user.registered.via_vk", plan="test")
    merged_id = await _registered(db, name="Дубль", event="user.registered.via_magic_link")
    await db.execute(
        text("UPDATE users SET is_active=false, merged_into_user_id=:t WHERE id=:u"),
        {"t": vk_id, "u": merged_id},
    )
    await db.execute(
        text(
            "INSERT INTO guest_session (attributed_user_id, attribution) "
            "VALUES (:u, CAST(:a AS jsonb))"
        ),
        {"u": vk_id, "a": '{"utm_source": "vk_ads", "utm_campaign": "ege"}'},
    )
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/new-users",
        params={"after_id": start, "min_age_sec": 0},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_id = {it["id"]: it for it in body["items"]}
    assert vk_id in by_id and anon_id in by_id
    assert test_id not in by_id and merged_id not in by_id
    assert by_id[vk_id]["channel"] == "vk"
    assert by_id[vk_id]["plan_code"] == "base"
    assert by_id[vk_id]["attribution"]["utm_source"] == "vk_ads"
    assert by_id[anon_id]["channel"] == "admin"
    assert by_id[anon_id]["full_name"] is None
    assert body["cursor"] >= merged_id

    again = await client.get(
        "/api/v1/admin/new-users",
        params={"after_id": body["cursor"], "min_age_sec": 0},
        headers=_auth(token),
    )
    ids = {it["id"] for it in again.json()["items"]}
    assert not ids & {vk_id, anon_id}


@pytest.mark.asyncio
async def test_fresh_users_wait(db, client):
    token = await _staff(db, "admin")
    start = (await db.execute(text("SELECT MAX(id) FROM users"))).scalar_one()
    uid = await _registered(db, name="Свежий", event="user.registered.via_tg_init")
    resp = await client.get(
        "/api/v1/admin/new-users",
        params={"after_id": start, "min_age_sec": 600},
        headers=_auth(token),
    )
    body = resp.json()
    assert uid not in {it["id"] for it in body["items"]}
    assert body["cursor"] < uid
