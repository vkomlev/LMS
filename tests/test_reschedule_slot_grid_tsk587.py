"""tsk-587: перенос и запись ученика идут по реальным слотам расписания.

Живой случай (прод, 2026-08-05): ученик 4530 перенёс занятие на среду 17:00 —
время, в которое нет ни одного слота. Занятие 4640 встало с `slot_id=NULL`
мимо расписания. Причин было две, и обе воспроизведены здесь:

1. Кандидаты строились из часов работы школы шагом в полчаса. Среда на проде
   открыта 10:00–12:00 и 13:00–19:00, а занятия в ней в 10:00, 11:00, 12:00 и
   18:00 — то есть 13:00, 13:30 … 17:30 предлагались как валидные.
2. Приём переноса проверял только часы работы, слот не проверял вовсе: через
   него проходило время, которого в списке не было.

Плюс продолжение tsk-464/443: попадание в слот сажает ученика в занятие ЭТОГО
слота, а не в параллельное занятие на одного человека (на проде таких нашлось
три: 917, 4207, 5674).
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.lesson_occurrence_teacher import LessonOccurrenceTeacher
from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_teacher import LessonSlotTeacher
from app.models.operating_hours import OperatingHours
from app.models.users import Users
from app.services import lesson_occurrence_service
from app.services.auth.session_service import create_session
from app.utils.exceptions import DomainError

MSK = ZoneInfo("Europe/Moscow")
WEDNESDAY = 2

# Реальная конфигурация прода на 2026-08-08: среда с перерывом 12:00-13:00
# (tsk-436/437, личное время оператора) и четыре занятия в течение дня.
PROD_WEDNESDAY_HOURS = ((time(10, 0), time(12, 0)), (time(13, 0), time(19, 0)))
PROD_WEDNESDAY_SLOTS = (time(10, 0), time(11, 0), time(12, 0), time(18, 0))


# ============================== Helpers ==============================


async def _create_user(db, *, role: str, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(user)
    await db.flush()
    row = (
        await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
    ).fetchone()
    if row is None:
        await db.execute(
            text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"), {"n": role},
        )
        row = (
            await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        ).fetchone()
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) ON CONFLICT DO NOTHING"),
        {"u": user.id, "r": int(row[0])},
    )
    await db.commit()
    return user.id


def _next_date_of_weekday(weekday: int, *, min_days_ahead: int = 1) -> date:
    """Ближайшая будущая дата с этим днём недели (по московскому календарю)."""
    today = datetime.now(MSK).date()
    for offset in range(min_days_ahead, min_days_ahead + 8):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() == weekday:
            return candidate
    raise AssertionError("день недели не найден в пределах недели")


async def _seed_wednesday_school(db, *, teacher_id: int) -> dict[time, int]:
    """Часы работы и слоты среды ровно как на проде. Возвращает id слотов."""
    for start, end in PROD_WEDNESDAY_HOURS:
        db.add(
            OperatingHours(
                weekday=WEDNESDAY, start_time=start, end_time=end, timezone="Europe/Moscow",
            )
        )
    slot_ids: dict[time, int] = {}
    for start in PROD_WEDNESDAY_SLOTS:
        slot = LessonSlot(
            teacher_id=teacher_id,
            weekday=WEDNESDAY,
            start_time=start,
            duration_minutes=60,
            timezone="Europe/Moscow",
            is_active=True,
        )
        db.add(slot)
        await db.flush()
        db.add(LessonSlotTeacher(slot_id=slot.id, teacher_id=teacher_id, is_active=True))
        slot_ids[start] = slot.id
    await db.commit()
    return slot_ids


async def _create_occurrence(
    db,
    *,
    teacher_id: int,
    scheduled_at: datetime,
    slot_id: int | None = None,
    student_ids: tuple[int, ...] = (),
    duration_minutes: int = 60,
) -> int:
    occurrence = LessonOccurrence(
        slot_id=slot_id,
        teacher_id=teacher_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occurrence)
    await db.flush()
    db.add(LessonOccurrenceTeacher(occurrence_id=occurrence.id, teacher_id=teacher_id))
    for student_id in student_ids:
        db.add(
            LessonOccurrenceParticipant(
                occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
            )
        )
    occurrence_id = occurrence.id
    await db.commit()
    return occurrence_id


def _msk(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=MSK).astimezone(dt_timezone.utc)


async def _count_occurrences_at(db, scheduled_at: datetime) -> int:
    return (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE scheduled_at = :at"),
            {"at": scheduled_at},
        )
    ).scalar_one()


# ============================== Выдача кандидатов ==============================


@pytest.mark.asyncio
async def test_candidates_contain_only_real_slot_times(db, client):
    """Главный регресс: в списке нет 13:00-17:30, есть только времена слотов."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )

    resp = await client.get(
        "/api/v1/lesson-occurrences/available-slots",
        params={"occurrence_id": occurrence_id, "limit": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    offered = [
        datetime.fromisoformat(item["scheduled_at"]).astimezone(MSK)
        for item in resp.json()
    ]
    assert offered, "хотя бы одно время слота должно предлагаться"

    for moment in offered:
        assert moment.weekday() == WEDNESDAY
        assert moment.time() in PROD_WEDNESDAY_SLOTS, (
            f"предложено {moment.time()} — такого занятия в расписании нет"
        )
        assert not (time(13, 0) <= moment.time() <= time(17, 30)), (
            "вернулась старая получасовая сетка внутри часов работы школы"
        )


@pytest.mark.asyncio
async def test_candidate_outside_operating_hours_is_dropped(db, client):
    """Часы работы остаются внешней рамкой: слот 12:00-13:00 попадает в
    перерыв среды и в список не выходит. Это осознанное следствие решения
    оператора (слоты + рамка), а не побочный эффект — если 12:00 нужно
    предлагать, чинить надо данные `operating_hours`, а не код."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )

    resp = await client.get(
        "/api/v1/lesson-occurrences/available-slots",
        params={"occurrence_id": occurrence_id, "limit": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    offered_times = {
        datetime.fromisoformat(item["scheduled_at"]).astimezone(MSK).time()
        for item in resp.json()
    }
    assert time(10, 0) in offered_times
    assert time(18, 0) in offered_times
    assert time(12, 0) not in offered_times


@pytest.mark.asyncio
async def test_candidates_skip_time_already_busy_for_student(db, client):
    """Занятое у самого ученика время из списка уходит."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    wednesday = _next_date_of_weekday(WEDNESDAY)
    busy_at = _msk(wednesday, time(10, 0))
    await _create_occurrence(
        db, teacher_id=teacher_id, scheduled_at=busy_at, student_ids=(student_id,),
    )
    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )

    resp = await client.get(
        "/api/v1/lesson-occurrences/available-slots",
        params={"occurrence_id": occurrence_id, "limit": 30},
        headers={"Authorization": f"Bearer {token}"},
    )
    offered = {datetime.fromisoformat(i["scheduled_at"]) for i in resp.json()}
    assert busy_at not in offered


# ============================== Приём переноса ==============================


@pytest.mark.asyncio
async def test_reschedule_to_time_without_slot_is_rejected(db, client):
    """Приём проверяет то же, что выдача: 17:00 среды больше не проходит."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )
    off_grid = _msk(_next_date_of_weekday(WEDNESDAY), time(17, 0))

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/reschedule",
        json={"new_scheduled_at": off_grid.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert await _count_occurrences_at(db, off_grid) == 0, (
        "занятие вне расписания не должно появиться даже частично"
    )


@pytest.mark.asyncio
async def test_reschedule_joins_occurrence_of_that_slot(db, client):
    """Попадание в слот сажает ученика в занятие ЭТОГО слота."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    classmate_id = await _create_user(db, role="student", prefix="tsk587-mate")
    token, _, _ = await create_session(db, user_id=student_id)
    slot_ids = await _seed_wednesday_school(db, teacher_id=teacher_id)

    wednesday = _next_date_of_weekday(WEDNESDAY)
    target = _msk(wednesday, time(11, 0))
    slot_occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=target,
        slot_id=slot_ids[time(11, 0)],
        student_ids=(classmate_id,),
    )
    old_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{old_id}/reschedule",
        json={"new_scheduled_at": target.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == slot_occurrence_id
    assert resp.json()["slot_id"] == slot_ids[time(11, 0)]
    assert await _count_occurrences_at(db, target) == 1, (
        "параллельного занятия на то же время быть не должно"
    )

    mate_status = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id = :oid AND student_id = :sid"
            ),
            {"oid": slot_occurrence_id, "sid": classmate_id},
        )
    ).scalar_one()
    assert mate_status == "scheduled", "участники слота не должны затрагиваться (tsk-435)"


@pytest.mark.asyncio
async def test_reschedule_creates_occurrence_bound_to_slot(db, client):
    """Занятия слота ещё нет (генератор не дошёл) — создаётся привязанное к
    слоту, а не отдельное с `slot_id=NULL`."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    slot_ids = await _seed_wednesday_school(db, teacher_id=teacher_id)

    target = _msk(_next_date_of_weekday(WEDNESDAY), time(18, 0))
    old_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{old_id}/reschedule",
        json={"new_scheduled_at": target.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slot_id"] == slot_ids[time(18, 0)]

    leads = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM lesson_occurrence_teacher "
                "WHERE occurrence_id = :oid AND teacher_id = :tid AND is_active"
            ),
            {"oid": body["id"], "tid": teacher_id},
        )
    ).scalar_one()
    assert leads == 1, "новое занятие должно быть видно преподавателю (tsk-443)"


@pytest.mark.asyncio
async def test_reschedule_to_same_time_rejected(db, client):
    """Перенос «на то же время» раньше плодил занятие-двойник, теперь 409."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    slot_ids = await _seed_wednesday_school(db, teacher_id=teacher_id)

    target = _msk(_next_date_of_weekday(WEDNESDAY), time(10, 0))
    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=target,
        slot_id=slot_ids[time(10, 0)],
        student_ids=(student_id,),
    )

    resp = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_id}/reschedule",
        json={"new_scheduled_at": target.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text


# ============================== Запись на отработку ==============================


@pytest.mark.asyncio
async def test_student_ad_hoc_off_grid_rejected(db, client):
    """Ученик не может обойти список прямым запросом к API."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    off_grid = _msk(_next_date_of_weekday(WEDNESDAY), time(17, 0))
    resp = await client.post(
        "/api/v1/lesson-occurrences/ad-hoc",
        json={
            "teacher_id": teacher_id,
            "scheduled_at": off_grid.isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_teacher_may_add_student_off_grid(db, client):
    """Преподавателю сетка не указ: отработка вне расписания — его право."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    token, _, _ = await create_session(db, user_id=teacher_id)
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    off_grid = _msk(_next_date_of_weekday(WEDNESDAY), time(17, 0))
    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_id,
            "scheduled_at": off_grid.isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["slot_id"] is None


@pytest.mark.asyncio
async def test_teacher_add_student_joins_existing_occurrence(db, client):
    """Регресс на занятия-двойники 917/4207/5674: ручное добавление на время
    уже существующего занятия не создаёт второе занятие на тот же час."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    classmate_id = await _create_user(db, role="student", prefix="tsk587-mate")
    token, _, _ = await create_session(db, user_id=teacher_id)
    slot_ids = await _seed_wednesday_school(db, teacher_id=teacher_id)

    target = _msk(_next_date_of_weekday(WEDNESDAY), time(10, 0))
    existing_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=target,
        slot_id=slot_ids[time(10, 0)],
        student_ids=(classmate_id,),
    )

    resp = await client.post(
        "/api/v1/teacher/lesson-occurrences/add-student",
        json={
            "teacher_id": teacher_id,
            "student_id": student_id,
            "scheduled_at": target.isoformat(),
            "duration_minutes": 60,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == existing_id
    assert await _count_occurrences_at(db, target) == 1


# ============================== Сервисный уровень ==============================


@pytest.mark.asyncio
async def test_service_rejects_off_grid_with_domain_error(db):
    """Проверка живёт в сервисе, а не только в роутере."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk587-teach")
    student_id = await _create_user(db, role="student", prefix="tsk587-stud")
    await _seed_wednesday_school(db, teacher_id=teacher_id)

    occurrence_id = await _create_occurrence(
        db,
        teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1),
        student_ids=(student_id,),
    )
    with pytest.raises(DomainError) as exc_info:
        await lesson_occurrence_service.reschedule_occurrence(
            db,
            occurrence_id=occurrence_id,
            student_id=student_id,
            new_scheduled_at=_msk(_next_date_of_weekday(WEDNESDAY), time(15, 30)),
        )
    assert exc_info.value.status_code == 422
