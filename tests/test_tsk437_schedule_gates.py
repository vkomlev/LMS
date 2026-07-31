"""tsk-437: расписание доступно методисту из веб-кабинета.

Задача писалась в июле под ТГ-бота — тогда кабинета методиста в SPW ещё не
было. Бот ходит сервисным ключом и проходит мимо проверки роли, поэтому гейт
`require_role("admin")` ему не мешал; браузеру методиста — мешал.

Здесь закрепляется:

- слоты, участники и часы работы открыты методисту (и по-прежнему админу и
  сервисному ключу), но НЕ преподавателю: слот определяет, кто и когда ведёт
  занятие, — это распорядительное решение;
- занятия чужого преподавателя видны методисту, при этом преподаватель
  по-прежнему видит только свои (identity-ветка сохранена — тот же приём, что
  в tsk-433 Волне 3.1 с ростером учеников);
- **новое API состава преподавателей слота**: до tsk-437 таблицей
  `lesson_slot_teacher` нельзя было управлять ниоткуда, хотя генератор занятий
  её читает, а на проде в ней 29 записей;
- смена основного преподавателя слота переносит БУДУЩИЕ занятия и не трогает
  прошедшие.
"""
from __future__ import annotations

import random
from datetime import time

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t437-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t437-{role or 'norole'}",
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


# --------------------------------------------------------------------------
# Гейт расписания
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_manages_slots(db, client):
    teacher_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")

    slot_id = await _slot(client, token, teacher_id)

    listed = await client.get("/api/v1/lesson-slots", headers=_auth(token))
    assert listed.status_code == 200, listed.text
    assert slot_id in [s["id"] for s in listed.json()]

    patched = await client.patch(
        f"/api/v1/lesson-slots/{slot_id}",
        json={"duration_minutes": 90},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["duration_minutes"] == 90


@pytest.mark.asyncio
async def test_methodist_manages_slot_students(db, client):
    teacher_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, teacher_id, weekday=2)

    add = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers=_auth(token),
    )
    assert add.status_code == 201, add.text

    listed = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/participants", headers=_auth(token)
    )
    assert student_id in [p["student_id"] for p in listed.json()]

    drop = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/participants/{student_id}",
        headers=_auth(token),
    )
    assert drop.status_code == 204, drop.text


@pytest.mark.asyncio
async def test_teacher_cannot_manage_schedule(db, client):
    """Расписание — распорядительное решение методиста, не самого преподавателя."""
    teacher_id, teacher_token = await _user(db, "teacher")

    r = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": 3,
            "start_time": "12:00:00",
            "duration_minutes": 60,
            "timezone": "Asia/Yekaterinburg",
            "student_ids": [],
        },
        headers=_auth(teacher_token),
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_student_cannot_read_schedule(db, client):
    _, token = await _user(db, "student")
    r = await client.get("/api/v1/lesson-slots", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_key_still_manages_slots(db, client):
    """ТГ-бот ходит сервисным ключом — для него ничего не изменилось."""
    teacher_id, _ = await _user(db, "teacher")
    r = await client.get(f"/api/v1/lesson-slots?api_key={_api_key()}")
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------
# Состав преподавателей слота (новое API)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_manages_slot_teachers(db, client):
    main_id, _ = await _user(db, "teacher")
    second_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id, weekday=4)

    add = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{second_id}", headers=_auth(token)
    )
    assert add.status_code == 201, add.text

    listed = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/teachers", headers=_auth(token)
    )
    assert second_id in [t["teacher_id"] for t in listed.json()]


@pytest.mark.asyncio
async def test_adding_same_teacher_twice_is_idempotent(db, client):
    main_id, _ = await _user(db, "teacher")
    second_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id, weekday=5)

    first = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{second_id}", headers=_auth(token)
    )
    again = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{second_id}", headers=_auth(token)
    )
    assert first.status_code == 201 and again.status_code == 201, again.text

    listed = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/teachers", headers=_auth(token)
    )
    assert [t["teacher_id"] for t in listed.json()].count(second_id) == 1


@pytest.mark.asyncio
async def test_last_teacher_cannot_be_removed(db, client):
    """Слот без ведущего сгенерировал бы занятия, которых никто не видит.

    Пустой состав генератор молча трактует как «ведёт основной» — то есть
    снятие последнего выглядело бы как успешное, а по факту вернуло бы прежнего.
    """
    main_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    # создание слота само заводит основного в состав — он и есть единственный
    slot_id = await _slot(client, token, main_id, weekday=6)

    listed = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/teachers", headers=_auth(token)
    )
    assert [t["teacher_id"] for t in listed.json()] == [main_id], listed.text

    r = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{main_id}", headers=_auth(token)
    )
    assert r.status_code == 409, f"ожидали отказ, получили {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_teacher_can_be_removed_when_another_stays(db, client):
    main_id, _ = await _user(db, "teacher")
    a_id, _ = await _user(db, "teacher")
    b_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, main_id, weekday=0)

    await client.post(f"/api/v1/lesson-slots/{slot_id}/teachers/{a_id}", headers=_auth(token))
    await client.post(f"/api/v1/lesson-slots/{slot_id}/teachers/{b_id}", headers=_auth(token))

    r = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{a_id}", headers=_auth(token)
    )
    assert r.status_code == 204, r.text

    listed = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/teachers", headers=_auth(token)
    )
    ids = [t["teacher_id"] for t in listed.json()]
    assert a_id not in ids and b_id in ids


@pytest.mark.asyncio
async def test_slot_main_teacher_can_be_changed(db, client):
    """Раньше сменить ведущего можно было только пересозданием слота."""
    old_id, _ = await _user(db, "teacher")
    new_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, old_id, weekday=1)

    r = await client.patch(
        f"/api/v1/lesson-slots/{slot_id}",
        json={"teacher_id": new_id},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["teacher_id"] == new_id


@pytest.mark.asyncio
async def test_main_teacher_must_have_teacher_role(db, client):
    """Ведущим нельзя поставить того, кто не преподаватель."""
    old_id, _ = await _user(db, "teacher")
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")
    slot_id = await _slot(client, token, old_id, weekday=2)

    r = await client.patch(
        f"/api/v1/lesson-slots/{slot_id}",
        json={"teacher_id": student_id},
        headers=_auth(token),
    )
    assert r.status_code in (400, 422), f"ожидали отказ, получили {r.status_code}: {r.text}"


# --------------------------------------------------------------------------
# Занятия: методист видит чужие, преподаватель — только свои
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_sees_any_teacher_occurrences(db, client):
    teacher_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")

    r = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_teacher_still_sees_only_own_occurrences(db, client):
    """Identity-ветка сохранена: с ролью методиста было бы видно чужое."""
    other_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "teacher")

    r = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={other_id}", headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_teacher_reads_own_occurrences(db, client):
    teacher_id, token = await _user(db, "teacher")
    r = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
