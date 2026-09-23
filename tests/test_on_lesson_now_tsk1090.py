"""tsk-1090: «кто сейчас на занятии» для подсветки в ленте преподавателя.

Источник — фаза плана занятия (tsk-743), а не часы расписания. Проверяем на
НАСТОЯЩЕЙ БД с подменённым «сейчас»: в фазе `start`/`during` ученик есть,
до начала и на итогах — нет; перенос, пропуск и перерыв не подсвечиваются;
чужое занятие — тоже.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import lesson_plan_service
from tests.test_lesson_plan_tsk743 import _new_user, _occurrence, _student_break

UTC = timezone.utc


@pytest.mark.asyncio
async def test_on_lesson_follows_plan_phase_and_skips_moved_absent_break(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="t1090")
    other_teacher, _ = await _new_user(db, role="teacher", name="o1090")
    here, _ = await _new_user(db, role="student", name="here")
    moved, _ = await _new_user(db, role="student", name="moved")
    absent, _ = await _new_user(db, role="student", name="absent")
    on_break, _ = await _new_user(db, role="student", name="brk")
    foreign, _ = await _new_user(db, role="student", name="foreign")

    start = (datetime.now(UTC) + timedelta(days=3)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    await _occurrence(
        db, teacher_id=teacher_id, scheduled_at=start,
        students={here: "confirmed", moved: "rescheduled", absent: "no_show",
                  on_break: "scheduled"},
    )
    await _occurrence(
        db, teacher_id=other_teacher, scheduled_at=start, students={foreign: "confirmed"},
    )
    await _student_break(
        db, student_id=on_break, starts_on=start.date(), ends_on=start.date(),
    )

    async def at(moment: datetime) -> list[int]:
        return await lesson_plan_service.students_on_lesson_now(
            db, teacher_id=teacher_id, now=moment,
        )

    # Середина урока — `during`: только пришедший, без перенёсшего,
    # отмеченного пропуска, перерыва и ученика чужого занятия.
    assert await at(start + timedelta(minutes=25)) == [here]
    # Самое начало — `start`.
    assert await at(start + timedelta(minutes=1)) == [here]
    # Задолго до начала и после конца урока — никого.
    assert await at(start - timedelta(hours=1)) == []
    assert await at(start + timedelta(hours=2)) == []
