"""tsk-563: методист/админ редактируют доп. поля профиля (категория/класс/
город/часовой пояс) чужого ученика через `PATCH /users/{id}`.

Переиспользует `_PEOPLE_WRITE_GATE` (methodist+admin) — тот же гейт, что уже
редактирует ФИО/почту (tsk-433 Волна 3.2). Валидация/каскад — та же функция
`me_service.update_profile_extra`, что self-service `PATCH /me` (tsk-427),
переиспользована напрямую, не продублирована.

Образец подъёма user+session — `test_tsk433_people_write_gates.py`.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user(db, role: str | None, email: str | None = None) -> tuple[int, str]:
    u = Users(
        email=email or f"t563-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t563-{role or 'norole'}",
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


# --------------------------------------------------------------------------
# Happy path: методист и админ
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_edits_profile_extra_of_student(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "school_student", "school_grade": 9, "city": "Казань", "timezone": "Asia/Yekaterinburg"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "school_student"
    assert body["school_grade"] == 9
    assert body["city"] == "Казань"
    assert body["timezone"] == "Asia/Yekaterinburg"

    card = await client.get(f"/api/v1/users/{target_id}", headers=_auth(token))
    assert card.json()["school_grade"] == 9


@pytest.mark.asyncio
async def test_admin_edits_profile_extra_of_student(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "admin")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "adult", "city": "Новосибирск"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "adult"
    assert r.json()["city"] == "Новосибирск"


@pytest.mark.asyncio
async def test_profile_extra_combined_with_full_name_in_one_request(db, client):
    """Доп. поля и ФИО в одном запросе — одна транзакция, оба применяются."""
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"full_name": "Петров Пётр", "category": "applicant", "city": "Омск"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Петров Пётр"
    assert r.json()["category"] == "applicant"
    assert r.json()["city"] == "Омск"


# --------------------------------------------------------------------------
# Кросс-валидация и каскад (та же логика, что в tsk-427)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grade_rejected_for_non_school_category(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "adult", "school_grade": 5},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_grade_reset_when_category_changes_away_from_school(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    setup = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "school_student", "school_grade": 11},
        headers=_auth(token),
    )
    assert setup.status_code == 200, setup.text

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "college_student"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "college_student"
    assert r.json()["school_grade"] is None


@pytest.mark.asyncio
async def test_invalid_timezone_rejected(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"timezone": "Not/AZone"},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------
# ACL: студент не может править чужой профиль
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_student_cannot_edit_someone_elses_profile_extra(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "student")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "adult"},
        headers=_auth(token),
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Audit event
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_extra_update_writes_audit_event(db, client):
    target_id, _ = await _user(db, "student")
    actor_id, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"category": "university_student", "city": "Тверь"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text(
                "SELECT user_id, details FROM audit_event "
                "WHERE event_type = 'admin.profile_extra.updated' "
                "ORDER BY id DESC LIMIT 1"
            )
        )
    ).mappings().first()
    assert row is not None
    assert row["user_id"] == actor_id
    assert row["details"]["target_user_id"] == target_id
    assert row["details"]["category"] == "university_student"
    assert row["details"]["city"] == "Тверь"


@pytest.mark.asyncio
async def test_full_name_only_update_does_not_write_profile_extra_audit(db, client):
    """Правка одного ФИО не должна плодить лишнее audit-событие про доп. поля."""
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"full_name": "Сидоров Сидор"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text(
                "SELECT id FROM audit_event "
                "WHERE event_type = 'admin.profile_extra.updated' "
                "AND (details->>'target_user_id')::int = :t"
            ),
            {"t": target_id},
        )
    ).first()
    assert row is None
