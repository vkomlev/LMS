"""tsk-746 — состав ведущих в ответе о слоте.

`lesson_slot.teacher_id` — основной/создатель, а кто реально ведёт, лежит в
`lesson_slot_teacher`. После перестановки преподавателей основной со слота может
быть снят, и расписание, показывающее только его, называет не того человека —
ровно это и нашлось на живой проверке 31.08: все 23 слота школы были подписаны
одним именем, хотя вели их трое разных людей.
"""
from datetime import time

import pytest

from app.services import lesson_calendar_service
from app.services.auth.session_service import create_session
from tests.test_lesson_calendar_tsk428 import _create_user


@pytest.mark.asyncio
async def test_slot_read_returns_actual_teachers(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk746-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    main_teacher = await _create_user(db, role="teacher", prefix="tsk746-main")
    co_teacher = await _create_user(db, role="teacher", prefix="tsk746-co")

    created = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": main_teacher,
            "weekday": 1,
            "start_time": "14:00:00",
            "duration_minutes": 60,
            "student_ids": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert created.status_code == 201, created.text
    slot_id = created.json()["id"]
    # Создатель попадает в состав сразу — иначе генератор занятий не знал бы,
    # кому их отдать.
    assert created.json()["teacher_ids"] == [main_teacher]

    added = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/teachers/{co_teacher}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert added.status_code == 201, added.text

    one = await client.get(
        f"/api/v1/lesson-slots/{slot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert one.status_code == 200
    assert set(one.json()["teacher_ids"]) == {main_teacher, co_teacher}

    listed = await client.get(
        "/api/v1/lesson-slots",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert listed.status_code == 200
    row = next(s for s in listed.json() if s["id"] == slot_id)
    assert set(row["teacher_ids"]) == {main_teacher, co_teacher}


@pytest.mark.asyncio
async def test_slot_read_shows_who_leads_after_main_teacher_removed(db, client):
    """Главный случай: основного сняли, ведёт другой — это должно быть видно."""
    admin_id = await _create_user(db, role="admin", prefix="tsk746-admin2")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    main_teacher = await _create_user(db, role="teacher", prefix="tsk746-main2")
    real_teacher = await _create_user(db, role="teacher", prefix="tsk746-real")

    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=main_teacher,
        weekday=3,
        start_time=time(16, 0),
        duration_minutes=60,
        timezone="Europe/Moscow",
        created_by=admin_id,
    )
    await lesson_calendar_service.add_slot_teacher(
        db, slot.id, real_teacher, added_by=admin_id
    )
    await lesson_calendar_service.remove_slot_teacher(db, slot.id, main_teacher)

    resp = await client.get(
        f"/api/v1/lesson-slots/{slot.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Основной остаётся в `teacher_id` — это его слот по учёту, — но ведёт
    # занятие другой, и расписание обязано показывать именно это.
    assert body["teacher_id"] == main_teacher
    assert body["teacher_ids"] == [real_teacher]
