"""tsk-1545: «последняя активность» не считает нулевые учебные сессии.

Дегенеративные сессии `learning_time_session` длительностью 0 с (открытие
страницы задания без работы) не должны делать «последнюю активность» новее
реальной работы. Реальные сессии (ended_at > started_at) — по-прежнему
считаются.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import methodist_students_dashboard_service as svc
from tests.test_methodist_students_dashboard_tsk1516 import _period_dates
from tests.test_methodist_students_dashboard_tsk1535 import (
    _active_student_with_solved_task,
)

UTC = timezone.utc


async def _insert_session(
    db, *, student_id: int, started_at: datetime, ended_at: datetime,
) -> None:
    """Сеанс как пишет learning_time_service._OPEN_SQL: opened/closed=now, seconds=0."""
    await db.execute(
        text(
            "INSERT INTO learning_time_session ("
            "  student_id, item_type, task_id, material_id, course_id,"
            "  started_at, ended_at, seconds, beats, interactions, source"
            ") VALUES ("
            "  :s, 'task', NULL, NULL, NULL,"
            "  :st, :en, 0, 1, 0, 'presence'"
            ")"
        ),
        {"s": student_id, "st": started_at, "en": ended_at},
    )
    await db.commit()


def _row(data: dict, sid: int) -> dict:
    for row in data["students"]:
        if row["student_id"] == sid:
            return row
    raise AssertionError(f"student {sid} not in dashboard")


@pytest.mark.asyncio
async def test_zero_second_session_does_not_move_last_activity(db):
    """0-с сессия позже последнего решения не двигает last_activity_at (tsk-1545)."""
    student_id = await _active_student_with_solved_task(db, tag="a1545zero")
    now = datetime.now(UTC)
    # Решение час назад; 0-секундная сессия — «сейчас» (открытие страницы).
    await _insert_session(db, student_id=student_id, started_at=now, ended_at=now)

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    la = _row(data, student_id)["last_activity_at"]
    assert la is not None
    # Дата активности — не новее решения (0-с сессия проигнорирована).
    assert datetime.fromisoformat(la) <= now - timedelta(minutes=30)


@pytest.mark.asyncio
async def test_real_session_moves_last_activity(db):
    """Сеанс с реальной длительностью позже решения двигает last_activity_at."""
    student_id = await _active_student_with_solved_task(db, tag="a1545real")
    now = datetime.now(UTC)
    started = now - timedelta(minutes=3)
    ended = now - timedelta(minutes=1)
    await _insert_session(db, student_id=student_id, started_at=started, ended_at=ended)

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)

    la = _row(data, student_id)["last_activity_at"]
    assert la is not None
    assert datetime.fromisoformat(la) == ended
