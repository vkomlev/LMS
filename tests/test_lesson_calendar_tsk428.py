"""tsk-428 (Календарь LMS, Фаза 1): генератор occurrence + admin API.

Покрывает:
- `_iter_occurrence_datetimes`: конвенция weekday (0=понедельник), горизонт,
  пропуск уже прошедшего сегодня времени слота.
- `lesson_occurrence_generator_tick`: генерация + идемпотентность (ON
  CONFLICT DO NOTHING по partial unique index) + пропуск неактивных слотов.
- Admin API `/lesson-slots`, `/operating-hours`: role-gate (403 не-admin),
  бизнес-валидация ролей пары (422), пересечение слотов (409), деактивация
  вместо удаления (204 + is_active=false, не физическое удаление).

Тесты используют общую откатываемую транзакцию из `tests/conftest.py`
(savepoint per test) — ручная очистка не нужна, но не помешает читаемости
там, где явно проверяется состояние между шагами.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timezone as dt_timezone

import pytest
from sqlalchemy import text

from app.models.lesson_slot import LessonSlot
from app.models.users import Users
from app.services.auth.session_service import create_session
from app.services.lesson_occurrence_generator_service import (
    _iter_occurrence_datetimes,
    lesson_occurrence_generator_tick,
)


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk428") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    if role:
        r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        row = r.fetchone()
        if row is None:
            await db.execute(
                text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                {"n": role},
            )
            r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
            row = r.fetchone()
        role_id = int(row[0])
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (:u, :r) ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


class _FakeSlot:
    """Лёгкая замена LessonSlot для юнит-тестов чистой функции — без похода в БД."""

    def __init__(self, weekday: int, start_time: time, timezone_name: str = "Europe/Moscow"):
        self.weekday = weekday
        self.start_time = start_time
        self.timezone = timezone_name


# ============================== _iter_occurrence_datetimes ==============================


def test_weekday_convention_monday_is_zero():
    """Конвенция: 0=понедельник, совпадает с Python date.weekday()."""
    assert date(2026, 7, 20).weekday() == 0  # sanity: контрольная дата — понедельник
    assert date(2026, 7, 26).weekday() == 6  # контрольная дата — воскресенье


def test_iter_occurrence_datetimes_basic_conversion():
    """Слот пн 10:00 MSK → первое будущее вхождение переведено в UTC (MSK=UTC+3, без DST)."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    now_utc = datetime(2026, 7, 20, 5, 0, tzinfo=dt_timezone.utc)  # понедельник, 08:00 MSK
    results = _iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now_utc)
    assert results[0] == datetime(2026, 7, 20, 7, 0, tzinfo=dt_timezone.utc)
    # Следующее вхождение — через 7 дней
    assert results[1] == datetime(2026, 7, 27, 7, 0, tzinfo=dt_timezone.utc)


def test_iter_occurrence_datetimes_skips_already_passed_today():
    """now позже времени слота сегодня → сегодняшнее вхождение не генерируется."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    # Понедельник, 12:00 MSK = 09:00 UTC — время слота (10:00 MSK) уже прошло
    now_utc = datetime(2026, 7, 20, 9, 0, tzinfo=dt_timezone.utc)
    results = _iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now_utc)
    assert results[0] == datetime(2026, 7, 27, 7, 0, tzinfo=dt_timezone.utc)


def test_iter_occurrence_datetimes_respects_horizon():
    """Горизонт в 3 дня от понедельника не должен включать вхождение через 7 дней."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    now_utc = datetime(2026, 7, 20, 5, 0, tzinfo=dt_timezone.utc)
    results = _iter_occurrence_datetimes(slot, horizon_days=3, now_utc=now_utc)
    assert len(results) == 1
    assert results[0] == datetime(2026, 7, 20, 7, 0, tzinfo=dt_timezone.utc)


# ============================== Generator tick (DB) ==============================


@pytest.mark.asyncio
async def test_generator_creates_occurrence_for_active_slot(db, db_session_factory):
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    slot = LessonSlot(
        student_id=student_id,
        teacher_id=teacher_id,
        weekday=date.today().weekday(),
        start_time=time(23, 59),  # заведомо ещё не наступило сегодня в большинстве TZ
        duration_minutes=60,
        timezone="Europe/Moscow",
        is_active=True,
    )
    db.add(slot)
    await db.flush()
    slot_id = slot.id
    await db.commit()

    summary = await lesson_occurrence_generator_tick(db_session_factory)
    assert summary["locked"] is True
    assert summary["active_slots"] >= 1

    rows = (
        await db.execute(
            text(
                "SELECT student_id, teacher_id, duration_minutes, status "
                "FROM lesson_occurrence WHERE slot_id = :sid"
            ),
            {"sid": slot_id},
        )
    ).fetchall()
    assert len(rows) >= 1
    row = rows[0]
    assert row[0] == student_id
    assert row[1] == teacher_id
    assert row[2] == 60
    assert row[3] == "scheduled"


@pytest.mark.asyncio
async def test_generator_idempotent_second_tick(db, db_session_factory):
    """Повторный тик не плодит дубли (ON CONFLICT DO NOTHING по slot_id+scheduled_at)."""
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    slot = LessonSlot(
        student_id=student_id,
        teacher_id=teacher_id,
        weekday=date.today().weekday(),
        start_time=time(23, 59),
        duration_minutes=45,
        timezone="Europe/Moscow",
        is_active=True,
    )
    db.add(slot)
    await db.flush()
    slot_id = slot.id
    await db.commit()

    summary1 = await lesson_occurrence_generator_tick(db_session_factory)
    count1 = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"),
            {"sid": slot_id},
        )
    ).scalar()

    summary2 = await lesson_occurrence_generator_tick(db_session_factory)
    count2 = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"),
            {"sid": slot_id},
        )
    ).scalar()

    assert summary1["locked"] is True and summary2["locked"] is True
    assert count1 == count2
    assert summary2["generated"] == 0  # второй тик не создал ни одной новой строки для этого слота


@pytest.mark.asyncio
async def test_generator_skips_inactive_slot(db, db_session_factory):
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    slot = LessonSlot(
        student_id=student_id,
        teacher_id=teacher_id,
        weekday=date.today().weekday(),
        start_time=time(23, 59),
        duration_minutes=30,
        timezone="Europe/Moscow",
        is_active=False,
    )
    db.add(slot)
    await db.flush()
    slot_id = slot.id
    await db.commit()

    await lesson_occurrence_generator_tick(db_session_factory)

    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"),
            {"sid": slot_id},
        )
    ).scalar()
    assert count == 0


# ============================== Admin API ==============================


@pytest.mark.asyncio
async def test_create_lesson_slot_admin_success(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "student_id": student_id,
            "teacher_id": teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["student_id"] == student_id
    assert body["teacher_id"] == teacher_id
    assert body["is_active"] is True
    assert body["timezone"] == "Europe/Moscow"


@pytest.mark.asyncio
async def test_create_lesson_slot_403_for_non_admin(db, client):
    other_id = await _create_user(db, prefix="tsk428-other")
    other_token, _, _ = await create_session(db, user_id=other_id)
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "student_id": student_id,
            "teacher_id": teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_create_lesson_slot_422_wrong_role_pair(db, client):
    """teacher_id без роли 'teacher' → 422 (защита от опечатки id в админке)."""
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    not_a_teacher_id = await _create_user(db, prefix="tsk428-plain")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "student_id": student_id,
            "teacher_id": not_a_teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_lesson_slot_409_overlap(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    payload = {
        "student_id": student_id,
        "teacher_id": teacher_id,
        "weekday": 3,
        "start_time": "12:00:00",
        "duration_minutes": 60,
    }
    resp1 = await client.post(
        "/api/v1/lesson-slots", json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 201, resp1.text

    # Тот же преподаватель, тот же день недели, пересекающееся время (12:30-13:30 vs 12:00-13:00)
    other_student_id = await _create_user(db, role="student", prefix="tsk428-stud2")
    payload2 = {
        "student_id": other_student_id,
        "teacher_id": teacher_id,
        "weekday": 3,
        "start_time": "12:30:00",
        "duration_minutes": 60,
    }
    resp2 = await client.post(
        "/api/v1/lesson-slots", json=payload2,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 409, resp2.text


@pytest.mark.asyncio
async def test_deactivate_lesson_slot_soft_delete(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "student_id": student_id,
            "teacher_id": teacher_id,
            "weekday": 4,
            "start_time": "09:00:00",
            "duration_minutes": 30,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    slot_id = resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 204, del_resp.text

    # Строка не удалена физически — is_active=false (сервисный API key: bypass роль-гейта)
    row = (
        await db.execute(
            text("SELECT is_active FROM lesson_slot WHERE id = :sid"), {"sid": slot_id}
        )
    ).fetchone()
    assert row is not None, "Слот должен остаться в БД (soft delete, не физическое удаление)"
    assert row[0] is False


@pytest.mark.asyncio
async def test_put_operating_hours_replaces_existing(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)

    resp1 = await client.put(
        "/api/v1/operating-hours",
        json={"weekday": 1, "start_time": "09:00:00", "end_time": "18:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 200, resp1.text

    resp2 = await client.put(
        "/api/v1/operating-hours",
        json={"weekday": 1, "start_time": "10:00:00", "end_time": "20:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 200, resp2.text

    rows = (
        await db.execute(text("SELECT start_time, end_time FROM operating_hours WHERE weekday = 1"))
    ).fetchall()
    assert len(rows) == 1, "PUT должен заменять запись на этот weekday, не плодить дубли"
    assert str(rows[0][0]) == "10:00:00"
