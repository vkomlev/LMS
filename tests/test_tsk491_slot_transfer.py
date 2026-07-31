"""tsk-491: перевод ученика между слотами одним действием.

Открепить и прикрепить по отдельности даёт тот же результат — но только если
не забыть второй шаг и если первый не упадёт на полпути. Здесь закрепляется,
что перевод неделим и что он не оставляет ученика в двух местах сразу.

Отдельно закрепляется починка ОТКРЕПЛЕНИЯ: оно гасило связь со слотом, но
оставляло ученика в уже созданных будущих занятиях — вопреки собственному
докстрингу. Расхождение было замечено ещё в tsk-455 (2026-07-28) и отложено;
на проде за это время накопился хвост (ученик 4526, слот 4, 2 будущих
занятия). Перевод на таком откреплении был бы сломан по построению: ученик
оказывался бы в списках явки обоих слотов.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t491-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t491-{role or 'norole'}",
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


async def _slot(
    client,
    token: str,
    teacher_id: int,
    *,
    weekday: int = 1,
    start: str = "10:00:00",
    duration: int = 60,
) -> int:
    r = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": weekday,
            "start_time": start,
            "duration_minutes": duration,
            "timezone": "Asia/Yekaterinburg",
            "student_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _future_occurrence(db, slot_id: int, teacher_id: int, *, days: int = 3) -> int:
    """Уже сгенерированное будущее занятие слота — генератор в тестах не гоняем."""
    row = await db.execute(
        text(
            "INSERT INTO lesson_occurrence "
            "(slot_id, teacher_id, scheduled_at, duration_minutes) "
            "VALUES (:s, :t, :at, 60) RETURNING id"
        ),
        {
            "s": slot_id,
            "t": teacher_id,
            "at": datetime.now(timezone.utc) + timedelta(days=days),
        },
    )
    occurrence_id = row.scalar_one()
    await db.commit()
    return occurrence_id


async def _participants(db, occurrence_id: int) -> list[tuple[int, str]]:
    rows = await db.execute(
        text(
            "SELECT student_id, status FROM lesson_occurrence_participant "
            "WHERE occurrence_id = :o ORDER BY student_id"
        ),
        {"o": occurrence_id},
    )
    return [(r.student_id, r.status) for r in rows]


async def _membership(db, slot_id: int, student_id: int) -> bool | None:
    row = await db.execute(
        text(
            "SELECT is_active FROM lesson_slot_student "
            "WHERE slot_id = :s AND student_id = :u"
        ),
        {"s": slot_id, "u": student_id},
    )
    value = row.scalar_one_or_none()
    return value


# --------------------------------------------------------------------------
# Починка открепления
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detach_clears_future_lessons(db, client):
    """Открепление убирает ученика из будущих занятий, а не только из слота."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, teacher_id)
    occurrence_id = await _future_occurrence(db, slot_id, teacher_id)

    add = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    assert add.status_code == 201, add.text
    assert (student_id, "scheduled") in await _participants(db, occurrence_id)

    removed = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/participants/{student_id}",
        headers=_auth(token),
    )
    assert removed.status_code == 204, removed.text
    assert await _membership(db, slot_id, student_id) is False
    assert student_id not in [s for s, _ in await _participants(db, occurrence_id)]


@pytest.mark.asyncio
async def test_detach_keeps_lessons_where_student_already_acted(db, client):
    """Своё решение ученика — история: подтверждённую явку открепление не стирает."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, teacher_id)
    occurrence_id = await _future_occurrence(db, slot_id, teacher_id)

    await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'confirmed' "
            "WHERE occurrence_id = :o AND student_id = :u"
        ),
        {"o": occurrence_id, "u": student_id},
    )
    await db.commit()

    await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/participants/{student_id}",
        headers=_auth(token),
    )
    assert (student_id, "confirmed") in await _participants(db, occurrence_id)


# --------------------------------------------------------------------------
# Перевод
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_moves_student_and_future_lessons(db, client):
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    target = await _slot(client, token, teacher_id, weekday=3, start="15:00:00")
    src_occurrence = await _future_occurrence(db, source, teacher_id)
    dst_occurrence = await _future_occurrence(db, target, teacher_id, days=4)

    await client.post(
        f"/api/v1/lesson-slots/{source}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )

    moved = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(token),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["slot_id"] == target

    assert await _membership(db, source, student_id) is False
    assert await _membership(db, target, student_id) is True
    assert student_id not in [s for s, _ in await _participants(db, src_occurrence)]
    assert (student_id, "scheduled") in await _participants(db, dst_occurrence)


@pytest.mark.asyncio
async def test_transfer_rejects_disabled_target(db, client):
    """В выключенный слот переводить некуда — ученик остался бы без занятий."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    target = await _slot(client, token, teacher_id, weekday=3, start="15:00:00")
    await client.post(
        f"/api/v1/lesson-slots/{source}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    await client.delete(f"/api/v1/lesson-slots/{target}", headers=_auth(token))

    r = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(token),
    )
    assert r.status_code == 409, r.text
    assert await _membership(db, source, student_id) is True


@pytest.mark.asyncio
async def test_transfer_rejects_time_conflict(db, client):
    """Ученик не может быть в двух местах в одно время.

    Пересекающиеся слоты возможны только у РАЗНЫХ преподавателей: для одного
    преподавателя наложение запрещено ещё на создании слота. Именно поэтому
    проверка нужна на уровне ученика, а не наследуется от проверки слота.
    """
    teacher_id, _ = await _user(db, "teacher")
    other_teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    # Целевой и уже занятый ученика — пересекающиеся окна среды у разных людей.
    target = await _slot(client, token, teacher_id, weekday=2, start="12:00:00")
    busy = await _slot(client, token, other_teacher_id, weekday=2, start="12:30:00")

    for slot_id in (source, busy):
        await client.post(
            f"/api/v1/lesson-slots/{slot_id}/participants",
            json={"student_id": student_id},
            headers=_auth(token),
        )

    r = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(token),
    )
    assert r.status_code == 409, r.text
    assert "то же время" in r.json()["detail"]
    assert await _membership(db, source, student_id) is True
    assert await _membership(db, target, student_id) is None


@pytest.mark.asyncio
async def test_transfer_allows_same_weekday_different_time(db, client):
    """Соседнее окно того же дня — не конфликт: отрезки не пересекаются."""
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    target = await _slot(client, token, teacher_id, weekday=2, start="12:00:00")
    await _slot(client, token, teacher_id, weekday=2, start="13:00:00")

    await client.post(
        f"/api/v1/lesson-slots/{source}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    r = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_transfer_rejects_same_slot(db, client):
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, teacher_id)
    await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )

    r = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants/{student_id}/transfer",
        json={"target_slot_id": slot_id},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_transfer_rejects_student_outside_source(db, client):
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    target = await _slot(client, token, teacher_id, weekday=3, start="15:00:00")

    r = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(token),
    )
    assert r.status_code == 404, r.text
    assert await _membership(db, target, student_id) is None


@pytest.mark.asyncio
async def test_transfer_closed_to_teacher(db, client):
    """Кто где учится — распорядительное решение, как и весь слот."""
    teacher_id, teacher_token = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    source = await _slot(client, token, teacher_id, weekday=0)
    target = await _slot(client, token, teacher_id, weekday=3, start="15:00:00")
    await client.post(
        f"/api/v1/lesson-slots/{source}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )

    r = await client.post(
        f"/api/v1/lesson-slots/{source}/participants/{student_id}/transfer",
        json={"target_slot_id": target},
        headers=_auth(teacher_token),
    )
    assert r.status_code == 403, r.text
