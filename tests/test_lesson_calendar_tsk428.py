"""tsk-428/435 (Календарь LMS): генератор occurrence + admin API, групповые слоты.

Покрывает:
- `iter_occurrence_datetimes`: конвенция weekday (0=понедельник), горизонт,
  пропуск уже прошедшего сегодня времени слота.
- `lesson_occurrence_generator_tick`: генерация occurrence + СИНК участников
  из `lesson_slot_student` в `lesson_occurrence_participant`, идемпотентность
  (ON CONFLICT DO NOTHING/DO UPDATE) + пропуск неактивных слотов.
- Admin API `/lesson-slots` (групповой, tsk-435), `/operating-hours`:
  role-gate (403 не-admin), бизнес-валидация ролей (422), пересечение слотов
  ТОЛЬКО по преподавателю (409 — участники больше не участвуют в этой
  проверке), деактивация вместо удаления, участники слота (add/list/remove).

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
from app.models.lesson_slot_student import LessonSlotStudent
from app.models.users import Users
from app.services.auth.session_service import create_session
from app.services.lesson_occurrence_generator_service import (
    iter_occurrence_datetimes,
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


async def _create_slot_with_students(
    db, *, teacher_id: int, student_ids: list[int],
    weekday: int, start_time: time, duration_minutes: int = 60,
) -> int:
    slot = LessonSlot(
        teacher_id=teacher_id,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
        timezone="Europe/Moscow",
        is_active=True,
    )
    db.add(slot)
    await db.flush()
    for student_id in student_ids:
        db.add(LessonSlotStudent(slot_id=slot.id, student_id=student_id, is_active=True))
    slot_id = slot.id
    await db.commit()
    return slot_id


class _FakeSlot:
    """Лёгкая замена LessonSlot для юнит-тестов чистой функции — без похода в БД."""

    def __init__(self, weekday: int, start_time: time, timezone_name: str = "Europe/Moscow"):
        self.weekday = weekday
        self.start_time = start_time
        self.timezone = timezone_name


# ============================== iter_occurrence_datetimes ==============================


def test_weekday_convention_monday_is_zero():
    """Конвенция: 0=понедельник, совпадает с Python date.weekday()."""
    assert date(2026, 7, 20).weekday() == 0  # sanity: контрольная дата — понедельник
    assert date(2026, 7, 26).weekday() == 6  # контрольная дата — воскресенье


def test_iter_occurrence_datetimes_basic_conversion():
    """Слот пн 10:00 MSK → первое будущее вхождение переведено в UTC (MSK=UTC+3, без DST)."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    now_utc = datetime(2026, 7, 20, 5, 0, tzinfo=dt_timezone.utc)  # понедельник, 08:00 MSK
    results = iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now_utc)
    assert results[0] == datetime(2026, 7, 20, 7, 0, tzinfo=dt_timezone.utc)
    # Следующее вхождение — через 7 дней
    assert results[1] == datetime(2026, 7, 27, 7, 0, tzinfo=dt_timezone.utc)


def test_iter_occurrence_datetimes_skips_already_passed_today():
    """now позже времени слота сегодня → сегодняшнее вхождение не генерируется."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    # Понедельник, 12:00 MSK = 09:00 UTC — время слота (10:00 MSK) уже прошло
    now_utc = datetime(2026, 7, 20, 9, 0, tzinfo=dt_timezone.utc)
    results = iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now_utc)
    assert results[0] == datetime(2026, 7, 27, 7, 0, tzinfo=dt_timezone.utc)


def test_iter_occurrence_datetimes_respects_horizon():
    """Горизонт в 3 дня от понедельника не должен включать вхождение через 7 дней."""
    slot = _FakeSlot(weekday=0, start_time=time(10, 0))
    now_utc = datetime(2026, 7, 20, 5, 0, tzinfo=dt_timezone.utc)
    results = iter_occurrence_datetimes(slot, horizon_days=3, now_utc=now_utc)
    assert len(results) == 1
    assert results[0] == datetime(2026, 7, 20, 7, 0, tzinfo=dt_timezone.utc)


# ============================== Generator tick (DB, групповой) ==============================


@pytest.mark.asyncio
async def test_generator_creates_occurrence_and_syncs_participants(db, db_session_factory):
    """Групповой слот (3 участника) → 1 occurrence + 3 lesson_occurrence_participant."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_ids = [
        await _create_user(db, role="student", prefix=f"tsk428-stud{i}") for i in range(3)
    ]

    slot_id = await _create_slot_with_students(
        db, teacher_id=teacher_id, student_ids=student_ids,
        weekday=date.today().weekday(), start_time=time(23, 59),
    )

    summary = await lesson_occurrence_generator_tick(db_session_factory)
    assert summary["locked"] is True
    assert summary["active_slots"] >= 1
    assert summary["participants_synced"] >= 3

    occ_rows = (
        await db.execute(
            text("SELECT id, teacher_id, duration_minutes FROM lesson_occurrence WHERE slot_id = :sid"),
            {"sid": slot_id},
        )
    ).fetchall()
    assert len(occ_rows) >= 1
    occurrence_id, occ_teacher_id, duration = occ_rows[0]
    assert occ_teacher_id == teacher_id
    assert duration == 60

    participant_rows = (
        await db.execute(
            text(
                "SELECT student_id, status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid"
            ),
            {"oid": occurrence_id},
        )
    ).fetchall()
    assert {r[0] for r in participant_rows} == set(student_ids)
    assert all(r[1] == "scheduled" for r in participant_rows)


@pytest.mark.asyncio
async def test_generator_idempotent_second_tick(db, db_session_factory):
    """Повторный тик не плодит дубли ни occurrence, ни участников."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")

    slot_id = await _create_slot_with_students(
        db, teacher_id=teacher_id, student_ids=[student_id],
        weekday=date.today().weekday(), start_time=time(23, 59), duration_minutes=45,
    )

    summary1 = await lesson_occurrence_generator_tick(db_session_factory)
    occ_count1 = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"), {"sid": slot_id},
        )
    ).scalar()
    part_count1 = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lo.slot_id = :sid"
            ),
            {"sid": slot_id},
        )
    ).scalar()

    summary2 = await lesson_occurrence_generator_tick(db_session_factory)
    occ_count2 = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"), {"sid": slot_id},
        )
    ).scalar()
    part_count2 = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM lesson_occurrence_participant lop "
                "JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
                "WHERE lo.slot_id = :sid"
            ),
            {"sid": slot_id},
        )
    ).scalar()

    assert summary1["locked"] is True and summary2["locked"] is True
    assert occ_count1 == occ_count2
    assert part_count1 == part_count2
    assert summary2["generated"] == 0
    assert summary2["participants_synced"] == 0


@pytest.mark.asyncio
async def test_generator_skips_inactive_slot(db, db_session_factory):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")

    slot = LessonSlot(
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
    db.add(LessonSlotStudent(slot_id=slot_id, student_id=student_id, is_active=True))
    await db.commit()

    await lesson_occurrence_generator_tick(db_session_factory)

    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"),
            {"sid": slot_id},
        )
    ).scalar()
    assert count == 0


# ============================== Admin API (групповой слот) ==============================


@pytest.mark.asyncio
async def test_create_lesson_slot_admin_success_with_participants(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    student_a = await _create_user(db, role="student", prefix="tsk428-studA")
    student_b = await _create_user(db, role="student", prefix="tsk428-studB")
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
            "student_ids": [student_a, student_b],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["teacher_id"] == teacher_id
    assert body["is_active"] is True
    assert body["timezone"] == "Europe/Moscow"
    assert set(body["student_ids"]) == {student_a, student_b}


@pytest.mark.asyncio
async def test_create_lesson_slot_403_for_non_admin(db, client):
    other_id = await _create_user(db, prefix="tsk428-other")
    other_token, _, _ = await create_session(db, user_id=other_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_create_lesson_slot_422_wrong_role_participant(db, client):
    """Один из student_ids без роли 'student' → 422 (защита от опечатки id в админке)."""
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    not_a_student_id = await _create_user(db, prefix="tsk428-plain")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id,
            "weekday": 2,
            "start_time": "15:00:00",
            "duration_minutes": 60,
            "student_ids": [not_a_student_id],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_lesson_slot_409_overlap_teacher_only(db, client):
    """Групповая модель: пересечение блокируется ТОЛЬКО по преподавателю —
    разные ученики на то же время того же учителя всё равно 409 (это должно
    решаться участниками ОДНОГО слота, не двумя разными слотами)."""
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_a = await _create_user(db, role="student", prefix="tsk428-studA")
    student_b = await _create_user(db, role="student", prefix="tsk428-studB")

    payload = {
        "teacher_id": teacher_id,
        "weekday": 3,
        "start_time": "12:00:00",
        "duration_minutes": 60,
        "student_ids": [student_a],
    }
    resp1 = await client.post(
        "/api/v1/lesson-slots", json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 201, resp1.text

    payload2 = {
        "teacher_id": teacher_id,
        "weekday": 3,
        "start_time": "12:30:00",
        "duration_minutes": 60,
        "student_ids": [student_b],
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
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
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

    row = (
        await db.execute(
            text("SELECT is_active FROM lesson_slot WHERE id = :sid"), {"sid": slot_id}
        )
    ).fetchone()
    assert row is not None, "Слот должен остаться в БД (soft delete, не физическое удаление)"
    assert row[0] is False


@pytest.mark.asyncio
async def test_post_operating_hours_allows_multiple_windows_same_weekday(db, client):
    """tsk-436/437: несколько окон на один weekday — норма (нужно вырезать
    перерыв внутри дня), не upsert-по-weekday, как было раньше."""
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)

    resp1 = await client.post(
        "/api/v1/operating-hours",
        json={"weekday": 1, "start_time": "09:00:00", "end_time": "12:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 201, resp1.text

    resp2 = await client.post(
        "/api/v1/operating-hours",
        json={"weekday": 1, "start_time": "13:00:00", "end_time": "19:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 201, resp2.text

    rows = (
        await db.execute(
            text("SELECT start_time, end_time FROM operating_hours WHERE weekday = 1 ORDER BY start_time")
        )
    ).fetchall()
    assert len(rows) == 2, "Два непересекающихся окна на один день должны сосуществовать"
    assert str(rows[0][0]) == "09:00:00" and str(rows[0][1]) == "12:00:00"
    assert str(rows[1][0]) == "13:00:00" and str(rows[1][1]) == "19:00:00"


@pytest.mark.asyncio
async def test_post_operating_hours_rejects_overlap_same_weekday(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin2")
    admin_token, _, _ = await create_session(db, user_id=admin_id)

    resp1 = await client.post(
        "/api/v1/operating-hours",
        json={"weekday": 2, "start_time": "09:00:00", "end_time": "18:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp1.status_code == 201, resp1.text

    resp2 = await client.post(
        "/api/v1/operating-hours",
        json={"weekday": 2, "start_time": "12:00:00", "end_time": "13:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp2.status_code == 409, resp2.text


@pytest.mark.asyncio
async def test_patch_delete_operating_hours(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin3")
    admin_token, _, _ = await create_session(db, user_id=admin_id)

    created = await client.post(
        "/api/v1/operating-hours",
        json={"weekday": 3, "start_time": "09:00:00", "end_time": "18:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/operating-hours/{row_id}",
        json={"end_time": "19:00:00"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["end_time"] == "19:00:00"

    deleted = await client.delete(
        f"/api/v1/operating-hours/{row_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deleted.status_code == 204, deleted.text

    remaining = (
        await db.execute(text("SELECT count(*) FROM operating_hours WHERE id = :id"), {"id": row_id})
    ).scalar()
    assert remaining == 0, "DELETE должен физически удалять запись (нет is_active у operating_hours)"


# ============================== Slot participants ==============================


@pytest.mark.asyncio
async def test_add_slot_participant_and_list(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={"teacher_id": teacher_id, "weekday": 5, "start_time": "10:00:00", "duration_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    slot_id = resp.json()["id"]

    add_resp = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert add_resp.status_code == 201, add_resp.text
    assert add_resp.json()["student_id"] == student_id

    list_resp = await client.get(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.status_code == 200
    assert [p["student_id"] for p in list_resp.json()] == [student_id]


@pytest.mark.asyncio
async def test_add_slot_participant_backfills_future_occurrence(db, client, db_session_factory):
    """Добавление участника в слот бэкфиллит уже сгенерированные будущие occurrence
    (не ждёт следующего тика генератора)."""
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")

    slot_id = await _create_slot_with_students(
        db, teacher_id=teacher_id, student_ids=[],
        weekday=date.today().weekday(), start_time=time(23, 59),
    )
    await lesson_occurrence_generator_tick(db_session_factory)

    occ_row = (
        await db.execute(
            text("SELECT id FROM lesson_occurrence WHERE slot_id = :sid"), {"sid": slot_id},
        )
    ).fetchone()
    assert occ_row is not None, "Occurrence должен быть сгенерирован до добавления участника"
    occurrence_id = occ_row[0]

    add_resp = await client.post(
        f"/api/v1/lesson-slots/{slot_id}/participants",
        json={"student_id": student_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert add_resp.status_code == 201, add_resp.text

    part_row = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": occurrence_id, "sid": student_id},
        )
    ).fetchone()
    assert part_row is not None, "Уже сгенерированный occurrence должен получить нового участника"
    assert part_row[0] == "scheduled"


@pytest.mark.asyncio
async def test_remove_slot_participant_soft(db, client):
    admin_id = await _create_user(db, role="admin", prefix="tsk428-admin")
    admin_token, _, _ = await create_session(db, user_id=admin_id)
    teacher_id = await _create_user(db, role="teacher", prefix="tsk428-teach")
    student_id = await _create_user(db, role="student", prefix="tsk428-stud")

    resp = await client.post(
        "/api/v1/lesson-slots",
        json={
            "teacher_id": teacher_id, "weekday": 6, "start_time": "11:00:00",
            "duration_minutes": 30, "student_ids": [student_id],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    slot_id = resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/lesson-slots/{slot_id}/participants/{student_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_resp.status_code == 204, del_resp.text

    row = (
        await db.execute(
            text(
                "SELECT is_active FROM lesson_slot_student "
                "WHERE slot_id = :sid AND student_id = :stid"
            ),
            {"sid": slot_id, "stid": student_id},
        )
    ).fetchone()
    assert row is not None and row[0] is False
