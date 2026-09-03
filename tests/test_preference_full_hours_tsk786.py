"""tsk-786 — сетка пожеланий различает «группы нет» и «группа набрана».

Живой случай 03.09: ученик щёлкал ЛЮБУЮ закрытую ячейку одинаково — и там, где
группы вообще нет, и там, где группа есть, но набрана под потолок (`BOOKING_MAX`,
tsk-746). Обе выглядели пустыми и вели к одному и тому же отказу 422. Экран
(SPW) теперь их различает, но для этого сервер должен отдавать `full_hours`
отдельно от `open_hours` — это и проверяют тесты ниже.
"""
from datetime import time

import pytest

from app.services import schedule_preference_service
from tests.test_lesson_calendar_tsk428 import _create_user, _create_slot_with_students


@pytest.mark.asyncio
async def test_full_group_hour_reported_separately_from_empty_hour(db):
    """Набранная группа (>8) — в `full_hours`, час без единой группы — ни там, ни там."""
    teacher = await _create_user(db, role="teacher", prefix="tsk786-t1")
    student = await _create_user(db, role="student", prefix="tsk786-s1")
    crowd = [await _create_user(db, role="student", prefix="tsk786-c1") for _ in range(9)]
    # Вторник 14:00 — группа на девять человек, потолок восемь.
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=crowd, weekday=1, start_time=time(hour=14)
    )

    data = await schedule_preference_service.get_preference(db, student)
    by_day = {d["weekday"]: d for d in data["grid"]}

    assert time(hour=14) in by_day[1]["full_hours"]
    assert time(hour=14) not in by_day[1]["open_hours"]
    # Четверг — группы нет вовсе: не открыт и не «полон», сетка про него молчит.
    assert by_day[3]["full_hours"] == []
    assert by_day[3]["open_hours"] == []


@pytest.mark.asyncio
async def test_full_hour_still_rejected_on_save(db, client):
    """Набранный час по-прежнему нельзя назвать пожеланием — правило не менялось."""
    from app.services.auth.session_service import create_session

    teacher = await _create_user(db, role="teacher", prefix="tsk786-t2")
    student = await _create_user(db, role="student", prefix="tsk786-s2")
    student_token, _, _ = await create_session(db, user_id=student)
    crowd = [await _create_user(db, role="student", prefix="tsk786-c2") for _ in range(9)]
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=crowd, weekday=2, start_time=time(hour=15)
    )

    resp = await client.put(
        "/api/v1/me/schedule-preference",
        json={
            "lessons_per_week": 1,
            "hours": [{"weekday": 2, "start_time": "15:00:00", "kind": "preferred"}],
        },
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "занятий нет" in resp.text


@pytest.mark.asyncio
async def test_own_full_group_hour_is_open_not_full(db):
    """Свой час всегда в `open_hours` — даже переполненный, и не дублируется в `full_hours`."""
    teacher = await _create_user(db, role="teacher", prefix="tsk786-t3")
    student = await _create_user(db, role="student", prefix="tsk786-s3")
    crowd = [await _create_user(db, role="student", prefix="tsk786-c3") for _ in range(9)]
    await _create_slot_with_students(
        db, teacher_id=teacher, student_ids=[student, *crowd], weekday=0, start_time=time(hour=13)
    )

    data = await schedule_preference_service.get_preference(db, student)
    by_day = {d["weekday"]: d for d in data["grid"]}

    assert time(hour=13) in by_day[0]["open_hours"]
    assert time(hour=13) not in by_day[0]["full_hours"]


@pytest.mark.asyncio
async def test_full_hours_empty_before_schedule_built(db):
    """Пока слотов нет вовсе, `full_hours` пуст у всех дней — сетка открыта целиком."""
    student = await _create_user(db, role="student", prefix="tsk786-early")

    data = await schedule_preference_service.get_preference(db, student)

    for day in data["grid"]:
        assert day["full_hours"] == [], f"день {day['weekday']}"


@pytest.mark.asyncio
async def test_two_teachers_same_hour_one_full_one_open_counts_as_open(db):
    """Час держит группы двух преподавателей — набранная группа одного не
    закрывает час, если у другого в этот же час есть место (ученик выбирает
    час, не преподавателя; см. фикс `full_h -= open_h`, найдено ревью tsk-786)."""
    teacher_full = await _create_user(db, role="teacher", prefix="tsk786-t4a")
    teacher_open = await _create_user(db, role="teacher", prefix="tsk786-t4b")
    student = await _create_user(db, role="student", prefix="tsk786-s4")
    crowd = [await _create_user(db, role="student", prefix="tsk786-c4") for _ in range(9)]
    await _create_slot_with_students(
        db, teacher_id=teacher_full, student_ids=crowd, weekday=0, start_time=time(hour=16),
    )
    await _create_slot_with_students(
        db, teacher_id=teacher_open, student_ids=[], weekday=0, start_time=time(hour=16),
    )

    data = await schedule_preference_service.get_preference(db, student)
    by_day = {d["weekday"]: d for d in data["grid"]}

    assert time(hour=16) in by_day[0]["open_hours"]
    assert time(hour=16) not in by_day[0]["full_hours"]
