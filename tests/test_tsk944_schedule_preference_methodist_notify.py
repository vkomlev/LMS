"""tsk-944: уведомление методисту о заполнении «Пожеланий к расписанию».

Раньше заполнение анкеты было видно только на сводке
(`GET /methodist/schedule-preferences/summary`) — методист узнавал об этом,
только если сам туда зашёл. Канал уведомления — тот же, что и у соседнего
`schedule_slot_request` (tsk-674 фаза 3, [[tsk-652]]): `notifications` →
`GET /methodist/escalations/pending` → поллер methodist-бота TG_LMS.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.schedule_preference import SchedulePreferenceWrite
from app.services import schedule_preference_service


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk944") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(user)
    await db.flush()
    if role:
        role_id = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "r": role_id},
        )
    await db.commit()
    return user.id


def _write(**kwargs) -> SchedulePreferenceWrite:
    payload = {
        "lessons_per_week": 2,
        "hours": [
            {"weekday": 0, "start_time": "17:00", "kind": "preferred"},
            {"weekday": 2, "start_time": "18:00", "kind": "preferred"},
            {"weekday": 5, "start_time": "10:00", "kind": "possible"},
        ],
    }
    payload.update(kwargs)
    return SchedulePreferenceWrite(**payload)


@pytest.mark.asyncio
async def test_save_notifies_methodist(db):
    methodist_id = await _create_user(db, role="methodist")
    student_id = await _create_user(db, role="student", prefix="tsk944-stud")

    await schedule_preference_service.save_preference(
        db, student_id, _write(comment="после 18 не могу"), changed_by=student_id
    )

    notif = (
        await db.execute(
            text(
                "SELECT title, payload FROM notifications "
                " WHERE user_id = :m AND kind = :k ORDER BY id DESC LIMIT 1"
            ),
            {"m": methodist_id, "k": schedule_preference_service.SUBMISSION_KIND},
        )
    ).first()
    assert notif is not None, "методист должен получить уведомление о заполнении анкеты"
    assert notif[1]["student_id"] == student_id
    assert notif[1]["lessons_per_week"] == 2
    assert notif[1]["preferred"] == ["пн 17:00", "ср 18:00"]
    assert notif[1]["possible"] == ["сб 10:00"]
    assert notif[1]["comment"] == "после 18 не могу"


@pytest.mark.asyncio
async def test_each_save_notifies_again(db):
    """Правка анкеты — отдельное событие, не дедуп прежней (в отличие от
    `schedule_slot_request`, где повторное нажатие обновляет ту же заявку):
    методисту важно видеть каждую версию, как и саму историю пожеланий."""
    methodist_id = await _create_user(db, role="methodist", prefix="tsk944-m2")
    student_id = await _create_user(db, role="student", prefix="tsk944-stud2")

    await schedule_preference_service.save_preference(db, student_id, _write(), changed_by=student_id)
    await schedule_preference_service.save_preference(
        db, student_id, _write(lessons_per_week=1), changed_by=student_id
    )

    count = (
        await db.execute(
            text(
                "SELECT count(*) FROM notifications WHERE user_id = :m AND kind = :k"
            ),
            {"m": methodist_id, "k": schedule_preference_service.SUBMISSION_KIND},
        )
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_no_methodists_does_not_raise(db):
    """Некому передать — предупреждение в лог, сохранение не падает."""
    student_id = await _create_user(db, role="student", prefix="tsk944-lonely")
    data = await schedule_preference_service.save_preference(
        db, student_id, _write(), changed_by=student_id
    )
    assert data["is_filled"] is True


@pytest.mark.asyncio
async def test_appears_in_methodist_escalations_endpoint(client, db):
    from app.core.config import Settings

    methodist_id = await _create_user(db, role="methodist", prefix="tsk944-m3")
    student_id = await _create_user(db, role="student", prefix="tsk944-stud3")
    await schedule_preference_service.save_preference(db, student_id, _write(), changed_by=student_id)

    api_key = next(iter(Settings().valid_api_keys))
    resp = await client.get(
        f"/api/v1/methodist/escalations/pending?limit=100&user_id={methodist_id}",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200
    kinds = [item["kind"] for item in resp.json()["items"]]
    assert schedule_preference_service.SUBMISSION_KIND in kinds
