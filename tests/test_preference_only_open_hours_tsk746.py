"""tsk-746 — пожелание можно поставить только на час, где занятие есть.

Опрос «когда вам удобно» задумывался ДО вёрстки: человек называл любые часы
сетки, и по ним собиралось расписание. После вёрстки то же поведение стало
ловушкой — 31.08 новичок отметил четверг 17:00, слота в этот час нет, и он
остался с одним занятием вместо двух, ничего об этом не узнав.

Поэтому: пока слотов нет, опрос работает по всей сетке (фаза 1 не сломана);
как только расписание составлено — выбирать можно только из часов с группами.
"""
from datetime import time

import pytest

from app.schemas.schedule_preference import SchedulePreferenceHour, SchedulePreferenceWrite
from app.services import schedule_preference_service
from app.services.auth.session_service import create_session
from tests.test_lesson_calendar_tsk428 import _create_user, _create_slot_with_students


@pytest.mark.asyncio
async def test_all_grid_is_open_while_no_slots_exist(db):
    """Расписания ещё нет — опрос работает по всей сетке, как в фазе 1."""
    student = await _create_user(db, role="student", prefix="tsk746-early")

    data = await schedule_preference_service.get_preference(db, student)

    for day in data["grid"]:
        assert day["open_hours"] == day["hours"], f"день {day['weekday']}"


@pytest.mark.asyncio
async def test_hour_without_slot_is_rejected(db, client):
    """Час без группы не принимается — с текстом, объясняющим что делать."""
    admin = await _create_user(db, role="admin", prefix="tsk746-adm")
    token, _, _ = await create_session(db, user_id=admin)
    teacher = await _create_user(db, role="teacher", prefix="tsk746-t")
    student = await _create_user(db, role="student", prefix="tsk746-s")
    student_token, _, _ = await create_session(db, user_id=student)
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=[], weekday=1, start_time=time(hour=17)
    )

    ok = await client.put(
        "/api/v1/me/schedule-preference",
        json={
            "lessons_per_week": 1,
            "hours": [{"weekday": 1, "start_time": "17:00:00", "kind": "preferred"}],
        },
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert ok.status_code == 200, ok.text

    # Четверг 17:00 — ровно тот случай, что был у новичка 31.08.
    bad = await client.put(
        "/api/v1/me/schedule-preference",
        json={
            "lessons_per_week": 1,
            "hours": [{"weekday": 3, "start_time": "17:00:00", "kind": "preferred"}],
        },
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert bad.status_code == 422, bad.text
    assert "занятий нет" in bad.text


@pytest.mark.asyncio
async def test_grid_marks_hours_with_groups(db):
    """Экран получает, какие часы открыты, — чтобы не рисовать их кнопками."""
    teacher = await _create_user(db, role="teacher", prefix="tsk746-t2")
    student = await _create_user(db, role="student", prefix="tsk746-s2")
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=[], weekday=1, start_time=time(hour=14)
    )

    data = await schedule_preference_service.get_preference(db, student)
    by_day = {d["weekday"]: d for d in data["grid"]}

    assert by_day[1]["open_hours"] == [time(hour=14)]
    assert by_day[3]["open_hours"] == []
    # Сама сетка не поменялась: экран рисует те же часы, но серыми.
    assert len(by_day[3]["hours"]) > 0


@pytest.mark.asyncio
async def test_own_hour_stays_open_even_when_group_is_full(db):
    """Свой час всегда можно назвать — человек в этой группе уже занимается."""
    teacher = await _create_user(db, role="teacher", prefix="tsk746-t3")
    student = await _create_user(db, role="student", prefix="tsk746-s3")
    crowd = [await _create_user(db, role="student", prefix="tsk746-c") for _ in range(9)]
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=[student, *crowd], weekday=2,
        start_time=time(hour=15)
    )

    data = await schedule_preference_service.get_preference(db, student)
    by_day = {d["weekday"]: d for d in data["grid"]}

    # В группе десять человек — записаться туда нельзя, но это его собственный час.
    assert time(hour=15) in by_day[2]["open_hours"]
