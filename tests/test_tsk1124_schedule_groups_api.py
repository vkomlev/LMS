"""tsk-1124 Ф2: API справочника групп расписания и групп ученика.

Покрывает:
- справочник: чтение методистом и преподавателем, запрет ученику; создание
  методистом, 409 на дубль названия, запрет выключить группу по умолчанию;
- группы ученика: без строк — эффективная группа по умолчанию; PUT заменяет
  набор целиком, пустой список возвращает в группу по умолчанию; 422 на
  выключенную группу и на не-ученика;
- слот: `group_id` в ответе, создание с группой, фильтр списка по группе, смена
  группы через PATCH, 404 на несуществующую группу.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth.session_service import create_session


async def _user(db, role: str) -> int:
    u = Users(
        email=f"tsk1124-{role}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"tsk1124-{role}", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = :n ON CONFLICT DO NOTHING"
        ),
        {"u": u.id, "n": role},
    )
    await db.commit()
    return u.id


async def _auth(db, role: str) -> tuple[int, dict]:
    uid = await _user(db, role)
    token, _, _ = await create_session(db, user_id=uid)
    return uid, {"Authorization": f"Bearer {token}"}


async def _gid(db, name: str) -> int:
    return (await db.execute(text("SELECT id FROM schedule_group WHERE name = :n"), {"n": name})).scalar_one()


@pytest.mark.asyncio
async def test_list_groups_by_role(db, client):
    _, methodist = await _auth(db, "methodist")
    _, teacher = await _auth(db, "teacher")
    _, student = await _auth(db, "student")
    resp = await client.get("/api/v1/schedule-groups", headers=methodist)
    assert resp.status_code == 200, resp.text
    names = [g["name"] for g in resp.json()]
    assert names[0] == "Дети · Информатика"  # группа по умолчанию первой
    assert "Взрослые · Тестирование" in names
    assert (await client.get("/api/v1/schedule-groups", headers=teacher)).status_code == 200
    assert (await client.get("/api/v1/schedule-groups", headers=student)).status_code == 403


@pytest.mark.asyncio
async def test_create_group_and_duplicate_and_default_guard(db, client):
    _, methodist = await _auth(db, "methodist")
    _, teacher = await _auth(db, "teacher")
    body = {"audience": "adults", "subject": "Python", "name": f"Взрослые · Python {random.randint(1, 10**6)}"}
    assert (await client.post("/api/v1/schedule-groups", json=body, headers=teacher)).status_code == 403
    resp = await client.post("/api/v1/schedule-groups", json=body, headers=methodist)
    assert resp.status_code == 201, resp.text
    assert resp.json()["is_default"] is False
    dup = await client.post("/api/v1/schedule-groups", json=body, headers=methodist)
    assert dup.status_code == 409, dup.text
    kids = await _gid(db, "Дети · Информатика")
    off = await client.patch(f"/api/v1/schedule-groups/{kids}", json={"is_active": False}, headers=methodist)
    assert off.status_code == 422, off.text


@pytest.mark.asyncio
async def test_student_groups_default_replace_and_reset(db, client):
    _, methodist = await _auth(db, "methodist")
    student_id = await _user(db, "student")
    kids = await _gid(db, "Дети · Информатика")
    adults = await _gid(db, "Взрослые · Тестирование")
    url = f"/api/v1/schedule-groups/students/{student_id}"

    resp = await client.get(url, headers=methodist)
    assert resp.json() == {"student_id": student_id, "group_ids": [], "effective_group_ids": [kids]}

    resp = await client.put(url, json={"group_ids": [adults]}, headers=methodist)
    assert resp.status_code == 200, resp.text
    assert resp.json()["effective_group_ids"] == [adults]

    resp = await client.put(url, json={"group_ids": [kids, adults]}, headers=methodist)
    assert sorted(resp.json()["group_ids"]) == sorted([kids, adults])

    resp = await client.put(url, json={"group_ids": []}, headers=methodist)
    assert resp.json()["group_ids"] == [] and resp.json()["effective_group_ids"] == [kids]


@pytest.mark.asyncio
async def test_student_groups_rejects_inactive_group_and_non_student(db, client):
    _, methodist = await _auth(db, "methodist")
    student_id = await _user(db, "student")
    teacher_id = await _user(db, "teacher")
    gid = (await db.execute(text(
        "INSERT INTO schedule_group (audience, subject, name, is_active) "
        "VALUES ('adults', 'Архив', :n, false) RETURNING id"
    ), {"n": f"tsk1124 выключенная {random.randint(1, 10**6)}"})).scalar_one()
    await db.commit()
    resp = await client.put(
        f"/api/v1/schedule-groups/students/{student_id}", json={"group_ids": [gid]}, headers=methodist,
    )
    assert resp.status_code == 422, resp.text
    resp = await client.put(
        f"/api/v1/schedule-groups/students/{teacher_id}", json={"group_ids": []}, headers=methodist,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_slot_group_create_filter_patch(db, client):
    _, methodist = await _auth(db, "methodist")
    teacher_id = await _user(db, "teacher")
    kids = await _gid(db, "Дети · Информатика")
    adults = await _gid(db, "Взрослые · Тестирование")
    base = {"teacher_id": teacher_id, "start_time": "12:00:00", "duration_minutes": 60}

    plain = await client.post("/api/v1/lesson-slots", json={**base, "weekday": 4}, headers=methodist)
    assert plain.status_code == 201, plain.text
    assert plain.json()["group_id"] == kids
    adult = await client.post(
        "/api/v1/lesson-slots", json={**base, "weekday": 3, "group_id": adults}, headers=methodist,
    )
    assert adult.status_code == 201, adult.text
    assert adult.json()["group_id"] == adults
    missing = await client.post(
        "/api/v1/lesson-slots", json={**base, "weekday": 2, "group_id": 999_999}, headers=methodist,
    )
    assert missing.status_code == 404, missing.text

    listed = await client.get(
        f"/api/v1/lesson-slots?teacher_id={teacher_id}&group_id={adults}", headers=methodist,
    )
    assert [s["id"] for s in listed.json()] == [adult.json()["id"]]

    moved = await client.patch(
        f"/api/v1/lesson-slots/{plain.json()['id']}", json={"group_id": adults}, headers=methodist,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["group_id"] == adults


@pytest.mark.asyncio
async def test_student_groups_roles_and_missing_user(db, client):
    _, methodist = await _auth(db, "methodist")
    _, teacher = await _auth(db, "teacher")
    student_id, student = await _auth(db, "student")
    url = f"/api/v1/schedule-groups/students/{student_id}"
    for headers in (teacher, student):
        assert (await client.get(url, headers=headers)).status_code == 403
        assert (await client.put(url, json={"group_ids": []}, headers=headers)).status_code == 403
    missing = await client.get("/api/v1/schedule-groups/students/999999999", headers=methodist)
    assert missing.status_code in (404, 422), missing.text


@pytest.mark.asyncio
async def test_staff_add_transfer_group_mismatch_and_force(db, client):
    """Методист ставит детского ученика во взрослый слот: 409 с признаком,
    с force_group — группа добавляется к детской, а не заменяет её."""
    _, methodist = await _auth(db, "methodist")
    teacher_id = await _user(db, "teacher")
    kid = await _user(db, "student")
    kids = await _gid(db, "Дети · Информатика")
    adults = await _gid(db, "Взрослые · Тестирование")
    base = {"teacher_id": teacher_id, "start_time": "12:00:00", "duration_minutes": 60}
    kid_slot = (await client.post("/api/v1/lesson-slots", json={**base, "weekday": 0}, headers=methodist)).json()["id"]
    adult_slot = (await client.post(
        "/api/v1/lesson-slots", json={**base, "weekday": 4, "group_id": adults}, headers=methodist,
    )).json()["id"]

    ok = await client.post(f"/api/v1/lesson-slots/{kid_slot}/participants", json={"student_id": kid}, headers=methodist)
    assert ok.status_code == 201, ok.text

    moved = await client.post(
        f"/api/v1/lesson-slots/{kid_slot}/participants/{kid}/transfer",
        json={"target_slot_id": adult_slot}, headers=methodist,
    )
    assert moved.status_code == 409, moved.text
    assert moved.json()["payload"]["code"] == "schedule_group_mismatch"

    added = await client.post(
        f"/api/v1/lesson-slots/{adult_slot}/participants",
        json={"student_id": kid}, headers=methodist,
    )
    assert added.status_code == 409 and added.json()["payload"]["slot_group_id"] == adults

    forced = await client.post(
        f"/api/v1/lesson-slots/{adult_slot}/participants",
        json={"student_id": kid, "force_group": True}, headers=methodist,
    )
    assert forced.status_code == 201, forced.text
    groups = (await client.get(f"/api/v1/schedule-groups/students/{kid}", headers=methodist)).json()
    assert sorted(groups["effective_group_ids"]) == sorted([kids, adults])


@pytest.mark.asyncio
async def test_create_slot_with_foreign_students_needs_force(db, client):
    _, methodist = await _auth(db, "methodist")
    teacher_id = await _user(db, "teacher")
    kid = await _user(db, "student")
    adults = await _gid(db, "Взрослые · Тестирование")
    body = {"teacher_id": teacher_id, "weekday": 4, "start_time": "12:00:00", "duration_minutes": 60,
            "group_id": adults, "student_ids": [kid]}
    refused = await client.post("/api/v1/lesson-slots", json=body, headers=methodist)
    assert refused.status_code == 409, refused.text
    created = await client.post("/api/v1/lesson-slots", json={**body, "force_group": True}, headers=methodist)
    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_teacher_lessons_carry_group(db, client):
    """Занятие в кабинете преподавателя несёт группу своего слота; разовое — группу по умолчанию."""
    from datetime import datetime, timedelta, timezone as tz

    teacher_id, teacher = await _auth(db, "teacher")
    adults = await _gid(db, "Взрослые · Тестирование")
    kids = await _gid(db, "Дети · Информатика")
    slot_id = (await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, group_id) "
        "VALUES (:t, 4, '12:00', 60, :g) RETURNING id"
    ), {"t": teacher_id, "g": adults})).scalar_one()
    at = datetime.now(tz.utc) + timedelta(days=2)
    for sid, shift in ((slot_id, 0), (None, 3)):
        await db.execute(text(
            "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, duration_minutes) "
            "VALUES (:s, :t, :at, 60)"
        ), {"s": sid, "t": teacher_id, "at": at + timedelta(hours=shift)})
    await db.commit()
    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences?teacher_id={teacher_id}", headers=teacher,
    )
    assert resp.status_code == 200, resp.text
    by_slot = {o["slot_id"]: o["group_id"] for o in resp.json()}
    assert by_slot == {slot_id: adults, None: kids}


@pytest.mark.asyncio
async def test_staff_force_unknown_student_is_not_500(db, client):
    """Несуществующий ученик с force_group — 404/422, а не 500 на внешнем ключе."""
    _, methodist = await _auth(db, "methodist")
    teacher_id = await _user(db, "teacher")
    adults = await _gid(db, "Взрослые · Тестирование")
    slot = (await client.post(
        "/api/v1/lesson-slots",
        json={"teacher_id": teacher_id, "weekday": 4, "start_time": "12:00:00",
              "duration_minutes": 60, "group_id": adults},
        headers=methodist,
    )).json()["id"]
    resp = await client.post(
        f"/api/v1/lesson-slots/{slot}/participants",
        json={"student_id": 999_999_999, "force_group": True}, headers=methodist,
    )
    assert resp.status_code in (404, 422), resp.text


@pytest.mark.asyncio
async def test_transfer_other_refusal_comes_before_group_and_rolls_back(db, client):
    """Перевод в выключенный слот чужой группы: отказ «выключен», а не вопрос про
    группу; с force_group группа ученику при отказе НЕ добавляется (откат)."""
    _, methodist = await _auth(db, "methodist")
    teacher_id = await _user(db, "teacher")
    kid = await _user(db, "student")
    adults = await _gid(db, "Взрослые · Тестирование")
    base = {"teacher_id": teacher_id, "start_time": "12:00:00", "duration_minutes": 60}
    kid_slot = (await client.post(
        "/api/v1/lesson-slots", json={**base, "weekday": 0, "student_ids": [kid]}, headers=methodist,
    )).json()["id"]
    adult_slot = (await client.post(
        "/api/v1/lesson-slots", json={**base, "weekday": 4, "group_id": adults}, headers=methodist,
    )).json()["id"]
    await client.delete(f"/api/v1/lesson-slots/{adult_slot}", headers=methodist)
    for force in (False, True):
        resp = await client.post(
            f"/api/v1/lesson-slots/{kid_slot}/participants/{kid}/transfer",
            json={"target_slot_id": adult_slot, "force_group": force}, headers=methodist,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json().get("payload", {}).get("code") != "schedule_group_mismatch"
    groups = (await client.get(f"/api/v1/schedule-groups/students/{kid}", headers=methodist)).json()
    assert groups["group_ids"] == []
