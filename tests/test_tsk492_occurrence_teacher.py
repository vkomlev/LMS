"""tsk-492: постоянный состав слота против разовых исключений на одно занятие.

Разделение, которое здесь закрепляется:

* **Постоянно** — состав СЛОТА. Действует и на будущие занятия, и на уже
  созданные: снятие со слота теперь освобождает их (зеркало tsk-491, где то же
  чинили для ученика).
* **Разово** — состав ОДНОГО занятия. Слот не меняется, следующие занятия идут
  как обычно.

Главная ловушка, ради которой всё и устроено гашением, а не удалением:
генератор занятий досыпает состав слота в будущие занятия каждый тик через
`ON CONFLICT DO NOTHING`. Он умеет только добавлять. Удалённую строку он вернёт
на следующем тике — то есть разовое снятие удалением НЕ работает в принципе.
Погашенную строку он не трогает.

Вторая ловушка: у занятия есть ещё колонка `teacher_id` («основной»). Снятие,
которое гасит только связь, для него ничего не меняет — а слоты школы заведены
именно на одного человека, так что это самый частый случай.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import lesson_occurrence_generator_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t492-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t492-{role or 'norole'}",
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


async def _slot(client, token: str, teacher_id: int, *, weekday: int = 1) -> int:
    r = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": weekday,
            "start_time": "10:00:00",
            "duration_minutes": 60,
            "timezone": "Asia/Yekaterinburg",
            "student_ids": [],
        },
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _occurrence(db, slot_id: int, teacher_id: int, *, days: int = 3) -> int:
    """Занятие слота с уже проставленным ведущим — как его создаёт генератор."""
    oid = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, duration_minutes) "
                "VALUES (:s, :t, :at, 60) RETURNING id"
            ),
            {"s": slot_id, "t": teacher_id, "at": datetime.now(timezone.utc) + timedelta(days=days)},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id) VALUES (:o, :t)"
        ),
        {"o": oid, "t": teacher_id},
    )
    await db.commit()
    return oid


async def _leading(db, occurrence_id: int) -> set[int]:
    rows = await db.execute(
        text(
            "SELECT teacher_id FROM lesson_occurrence_teacher "
            "WHERE occurrence_id = :o AND is_active"
        ),
        {"o": occurrence_id},
    )
    return set(rows.scalars().all())


# --------------------------------------------------------------------------
# Постоянно: состав слота
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slot_removal_frees_future_lessons(db, client):
    """Снятие со слота действует постоянно — и на уже созданные занятия."""
    main_id, _ = await _user(db, "teacher")
    second_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)
    # Добавление в состав слота подхватывает уже созданные будущие занятия.
    await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{second_id}", headers=_auth(token)
    )
    assert second_id in await _leading(db, occurrence_id)

    r = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{second_id}", headers=_auth(token)
    )
    assert r.status_code == 204, r.text
    assert second_id not in await _leading(db, occurrence_id)


# --------------------------------------------------------------------------
# Разово: состав одного занятия
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_off_add_does_not_touch_slot(db, client):
    main_id, _ = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)

    r = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{sub_id}",
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_one_off"] is True
    assert await _leading(db, occurrence_id) == {main_id, sub_id}

    # состав слота не изменился
    slot_teachers = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/teachers", headers=_auth(token)
    )
    assert [t["teacher_id"] for t in slot_teachers.json()] == [main_id]


@pytest.mark.asyncio
async def test_one_off_removal_survives_generator(db, client, db_session_factory):
    """Разовое снятие переживает тик генератора — иначе оно бессмысленно."""
    main_id, _ = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)
    await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{sub_id}",
        headers=_auth(token),
    )

    # подмена: штатного снимаем с ЭТОГО занятия
    r = await client.delete(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{main_id}",
        headers=_auth(token),
    )
    assert r.status_code == 204, r.text
    assert await _leading(db, occurrence_id) == {sub_id}

    # Фабрика сессий — тестовая: генератор со своей полез бы во второе
    # соединение и подрался бы с сессией теста за него.
    await lesson_occurrence_generator_service.lesson_occurrence_generator_tick(
        db_session_factory
    )
    assert await _leading(db, occurrence_id) == {sub_id}, (
        "генератор вернул снятого — значит снятие держится только до тика"
    )


@pytest.mark.asyncio
async def test_one_off_removal_hides_lesson_from_teacher(db, client):
    """Снятый не видит занятие, хотя числится основным по колонке."""
    main_id, main_token = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)
    await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{sub_id}",
        headers=_auth(token),
    )

    seen_before = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={main_id}", headers=_auth(main_token)
    )
    assert occurrence_id in [o["id"] for o in seen_before.json()]

    await client.delete(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{main_id}",
        headers=_auth(token),
    )
    seen_after = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={main_id}", headers=_auth(main_token)
    )
    assert occurrence_id not in [o["id"] for o in seen_after.json()]


@pytest.mark.asyncio
async def test_one_off_add_survives_slot_removal(db, client):
    """Разовое назначение — отдельное решение: чистка по слоту его не касается."""
    main_id, _ = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)

    # тот же человек и в составе слота, и поставлен разово на это занятие
    await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{sub_id}", headers=_auth(token)
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_teacher SET is_one_off = true "
            "WHERE occurrence_id = :o AND teacher_id = :t"
        ),
        {"o": occurrence_id, "t": sub_id},
    )
    await db.commit()

    await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{sub_id}", headers=_auth(token)
    )
    assert sub_id in await _leading(db, occurrence_id)


@pytest.mark.asyncio
async def test_last_teacher_of_lesson_cannot_be_removed(db, client):
    main_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)

    r = await client.delete(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{main_id}",
        headers=_auth(token),
    )
    assert r.status_code == 409, r.text
    assert await _leading(db, occurrence_id) == {main_id}


@pytest.mark.asyncio
async def test_one_off_add_can_undo_removal(db, client):
    """Передумали: поставить обратно того, кого сняли с этого занятия."""
    main_id, _ = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)
    await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{sub_id}",
        headers=_auth(token),
    )
    await client.delete(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{main_id}",
        headers=_auth(token),
    )

    r = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{main_id}",
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert await _leading(db, occurrence_id) == {main_id, sub_id}


@pytest.mark.asyncio
async def test_occurrence_teachers_closed_to_teacher(db, client):
    """Кто ведёт занятие — распорядительное решение, как и весь слот."""
    main_id, main_token = await _user(db, "teacher")
    sub_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id)
    occurrence_id = await _occurrence(db, slot_id, main_id)

    r = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/teachers/{sub_id}",
        headers=_auth(main_token),
    )
    assert r.status_code == 403, r.text
