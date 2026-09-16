"""tsk-964: двойной перенос занятия туда-обратно не должен создавать цикл
`rescheduled<->rescheduled`.

Живой инцидент (прод, 16.09, ученик Шахназарян Артур id=4614): перенос
occurrence A -> B, а через 9 секунд обратно B -> A. `_seat_student_at`
находило старую строку участия ученика на occurrence A (оставшуюся от
исходной записи, теперь `rescheduled` после первого переноса) и возвращала
её как есть, не реактивируя. Итог — обе строки участия (A и B) оказались в
статусе `rescheduled`, указывая друг на друга, ни одна не `scheduled` —
ученик остался без активного места вообще.

Фикс — `_reactivate_dead_participant` в `app/services/lesson_occurrence_service.py`:
если найденная существующая строка участия в мёртвом статусе
(`rescheduled`/`declined`/`no_show`), она возвращается в `scheduled` вместо
того чтобы отдаваться как есть. Применяется в трёх местах с одним и тем же
паттерном «есть строка — вернуть как есть»: `_seat_student_at` (join через
ad-hoc/reschedule), `join_occurrence_as_student` (самостоятельное
присоединение), `add_participant_to_occurrence` (ручное добавление
преподавателем).
"""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone as dt_timezone
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

MSK = ZoneInfo("Europe/Moscow")
WEDNESDAY = 2

PROD_WEDNESDAY_HOURS = ((time(10, 0), time(12, 0)), (time(13, 0), time(19, 0)))
PROD_WEDNESDAY_SLOTS = (time(10, 0), time(11, 0), time(12, 0), time(18, 0))


async def _create_user(db, *, role: str, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(user)
    await db.flush()
    row = (await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})).fetchone()
    if row is None:
        await db.execute(
            text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"), {"n": role},
        )
        row = (await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})).fetchone()
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) ON CONFLICT DO NOTHING"),
        {"u": user.id, "r": int(row[0])},
    )
    await db.commit()
    return user.id


def _next_date_of_weekday(weekday: int, *, min_days_ahead: int = 1):
    today = datetime.now(MSK).date()
    for offset in range(min_days_ahead, min_days_ahead + 8):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() == weekday:
            return candidate
    raise AssertionError("день недели не найден в пределах недели")


async def _seed_wednesday_school(db, *, teacher_id: int) -> dict[time, int]:
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
    db, *, teacher_id: int, scheduled_at: datetime, slot_id: int | None = None,
    student_ids: tuple[int, ...] = (), duration_minutes: int = 60,
) -> int:
    occurrence = LessonOccurrence(
        slot_id=slot_id, teacher_id=teacher_id, scheduled_at=scheduled_at,
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


def _msk(day, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=MSK).astimezone(dt_timezone.utc)


async def _participant_rows(db, *, student_id: int, occurrence_ids: tuple[int, ...]):
    rows = (
        await db.execute(
            text(
                "SELECT occurrence_id, status, rescheduled_to_occurrence_id "
                "FROM lesson_occurrence_participant "
                "WHERE student_id = :sid AND occurrence_id = ANY(:oids)"
            ),
            {"sid": student_id, "oids": list(occurrence_ids)},
        )
    ).mappings().fetchall()
    return {r["occurrence_id"]: r for r in rows}


@pytest.mark.asyncio
async def test_reschedule_back_and_forth_does_not_create_cycle(db, client):
    """Перенос A->B, затем в течение нескольких секунд B->A обратно —
    результат должен быть ОДНА активная запись `scheduled` на исходном A,
    без цикла `rescheduled<->rescheduled` (tsk-964, живой инцидент 16.09)."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk964-teach")
    student_id = await _create_user(db, role="student", prefix="tsk964-stud")
    token, _, _ = await create_session(db, user_id=student_id)
    slot_ids = await _seed_wednesday_school(db, teacher_id=teacher_id)

    wednesday = _next_date_of_weekday(WEDNESDAY)
    time_a = _msk(wednesday, time(18, 0))  # исходное место ученика (как occurrence 16608)
    time_b = _msk(wednesday, time(11, 0))  # куда он перенёсся первым переносом (как 16544)

    occurrence_a_id = await _create_occurrence(
        db, teacher_id=teacher_id, scheduled_at=time_a,
        slot_id=slot_ids[time(18, 0)], student_ids=(student_id,),
    )

    resp_a_to_b = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_a_id}/reschedule",
        json={"new_scheduled_at": time_b.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_a_to_b.status_code == 201, resp_a_to_b.text
    occurrence_b_id = resp_a_to_b.json()["id"]
    assert occurrence_b_id != occurrence_a_id

    # "через 9 секунд" — в тесте сразу следующий вызов, порядок и есть суть бага.
    resp_b_to_a = await client.post(
        f"/api/v1/lesson-occurrences/{occurrence_b_id}/reschedule",
        json={"new_scheduled_at": time_a.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_b_to_a.status_code == 201, resp_b_to_a.text
    assert resp_b_to_a.json()["id"] == occurrence_a_id, (
        "второй перенос обязан вернуть в уже существующее occurrence A, "
        "а не создать третье"
    )

    rows = await _participant_rows(
        db, student_id=student_id, occurrence_ids=(occurrence_a_id, occurrence_b_id)
    )
    assert len(rows) == 2, f"ожидались ровно 2 строки участия, получено: {rows}"

    scheduled = [r for r in rows.values() if r["status"] == "scheduled"]
    assert len(scheduled) == 1, (
        f"должно быть ровно ОДНО активное место, а не цикл rescheduled<->rescheduled: {rows}"
    )
    assert scheduled[0]["occurrence_id"] == occurrence_a_id, (
        "активным должно остаться последнее выбранное время — occurrence A"
    )
    assert rows[occurrence_b_id]["status"] == "rescheduled"
    assert rows[occurrence_b_id]["rescheduled_to_occurrence_id"] == occurrence_a_id
    assert rows[occurrence_a_id]["rescheduled_to_occurrence_id"] is None


@pytest.mark.asyncio
async def test_seat_student_at_reactivates_dead_participant_row():
    """Прямая проверка причины на уровне хелпера: мёртвая строка (rescheduled/
    declined/no_show) реактивируется, а не возвращается как есть."""
    for dead_status in ("rescheduled", "declined", "no_show"):
        participant = LessonOccurrenceParticipant(
            occurrence_id=1, student_id=1, status=dead_status,
            rescheduled_to_occurrence_id=42,
        )
        lesson_occurrence_service._reactivate_dead_participant(participant)
        assert participant.status == "scheduled", dead_status
        assert participant.rescheduled_to_occurrence_id is None, dead_status

    # "completed" — занятие уже состоялось, реактивировать явку в прошлом нельзя.
    completed = LessonOccurrenceParticipant(
        occurrence_id=1, student_id=1, status="completed", rescheduled_to_occurrence_id=None,
    )
    lesson_occurrence_service._reactivate_dead_participant(completed)
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_join_occurrence_reactivates_previously_rescheduled_row(db):
    """Тот же класс цикла на пути join (не reschedule): ученик когда-то
    перенёс явку с этого occurrence (строка стала `rescheduled`), а потом
    присоединяется к нему заново напрямую — должен получить активное место,
    не мёртвую строку."""
    teacher_id = await _create_user(db, role="teacher", prefix="tsk964-jteach")
    student_id = await _create_user(db, role="student", prefix="tsk964-jstud")
    occurrence_id = await _create_occurrence(
        db, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=2),
    )
    other_occurrence_id = await _create_occurrence(
        db, teacher_id=teacher_id,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=3),
    )
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=occurrence_id, student_id=student_id,
            status="rescheduled", rescheduled_to_occurrence_id=other_occurrence_id,
        )
    )
    await db.commit()

    occurrence, participant = await lesson_occurrence_service.join_occurrence_as_student(
        db, occurrence_id=occurrence_id, student_id=student_id,
    )
    assert occurrence.id == occurrence_id
    assert participant.status == "scheduled"
    assert participant.rescheduled_to_occurrence_id is None
