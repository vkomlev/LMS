"""tsk-1024: список расхождений ручной цены и расписания для персонала.

На настоящей БД, по образцу test_tsk557_price_inferred_norm.py.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk1024"
_URL = "/api/v1/staff/price-schedule-mismatches"


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
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


async def _new_group(db, name: str, tariffs: list[tuple[str, int, str | None, str | None]]) -> int:
    group_id = (
        await db.execute(
            text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
            {"n": f"{_TAG}-{name}-{random.randint(10**6, 10**7)}"},
        )
    ).scalar()
    for idx, (tname, price, kind, value) in enumerate(tariffs):
        await db.execute(
            text(
                "INSERT INTO pricing_tariff "
                "(group_id, name, price_minor, match_kind, match_value, sort_order) "
                "VALUES (:g, :n, :p, :k, :v, :s)"
            ),
            {"g": group_id, "n": tname, "p": price, "k": kind, "v": value, "s": idx},
        )
    await db.commit()
    return group_id


async def _set_override(db, *, student_id: int, group_id: int, price_minor: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_price_override (student_id, group_id, price_minor) "
            "VALUES (:s, :g, :p)"
        ),
        {"s": student_id, "g": group_id, "p": price_minor},
    )
    await db.commit()


async def _create_slot(db, *, teacher_id: int, student_id: int, weekday: int) -> int:
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
                "VALUES (:t, :wd, '10:00', 60) RETURNING id"
            ),
            {"t": teacher_id, "wd": weekday},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": slot_id, "u": student_id},
    )
    await db.commit()
    return int(slot_id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _setup(db, *, price: int, slots: int) -> int:
    teacher_id, _ = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    group_id = await _new_group(
        db, "g", [("2 раза в неделю", 550000, "attendance_frequency", "2"),
                  ("1 раз в неделю", 275000, "attendance_frequency", "1")]
    )
    await _set_override(db, student_id=student_id, group_id=group_id, price_minor=price)
    for wd in range(slots):
        await _create_slot(db, teacher_id=teacher_id, student_id=student_id, weekday=wd)
    return student_id


@pytest.mark.asyncio
async def test_mismatch_listed_with_details(db, client):
    """Ручная цена «2 раза», а в расписании один слот — в списке, с деталями."""
    student_id = await _setup(db, price=550000, slots=1)
    _, token = await _new_user(db, role="marketer", name="mk")
    resp = await client.get(_URL, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["student_id"] == student_id)
    assert row["price_kind"] == "manual"
    assert row["manual_price_minor"] == 550000
    assert row["price_weekly_lessons"] == 2
    assert row["schedule_weekly_lessons"] == 1
    assert row["schedule_tariff_name"] == "1 раз в неделю"
    assert row["schedule_tariff_price_minor"] == 275000


@pytest.mark.asyncio
async def test_matching_price_not_listed(db, client):
    """Цена совпадает со ступенью расписания — расхождения нет."""
    student_id = await _setup(db, price=550000, slots=2)
    _, token = await _new_user(db, role="teacher", name="t2")
    resp = await client.get(_URL, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert all(r["student_id"] != student_id for r in resp.json())


@pytest.mark.asyncio
async def test_student_and_parent_forbidden(db, client):
    """Ученику и родителю список недоступен."""
    for role in ("student", "parent"):
        _, token = await _new_user(db, role=role, name=f"x-{role}")
        resp = await client.get(_URL, headers=_auth(token))
        assert resp.status_code == 403, (role, resp.text)


@pytest.mark.asyncio
async def test_teacher_sees_only_own_students(db, client):
    """Преподаватель без закрепления чужое расхождение не видит."""
    student_id = await _setup(db, price=550000, slots=1)
    teacher_id, token = await _new_user(db, role="teacher", name="t3")
    resp = await client.get(_URL, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert all(r["student_id"] != student_id for r in resp.json())

    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()
    resp = await client.get(_URL, headers=_auth(token))
    assert any(r["student_id"] == student_id for r in resp.json())
