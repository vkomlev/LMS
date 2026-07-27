"""tsk-443: несколько преподавателей на одном занятии (совместное ведение).

Оператор: ученики должны быть видны сразу всем преподавателям одного
занятия, явка общая (не отметился ни у кого = пропуск). Архитектура:
ОДНО lesson_occurrence на несколько преподавателей (M2M lesson_slot_teacher
+ lesson_occurrence_teacher), а не отдельное occurrence на каждого —
"общая явка" получается бесплатно (один список участников).

Покрывает:
- `create_lesson_slot` сразу добавляет основного преподавателя в
  `lesson_slot_teacher` (без этого сам создатель не увидел бы свой слот).
- `add_slot_teacher`/`remove_slot_teacher`: идемпотентность, бэкфилл уже
  сгенерированных будущих occurrence, 404 на повторное удаление.
- `has_overlap` ловит пересечение и для со-преподавателя (не только основного).
- Генератор синхронизирует `lesson_occurrence_teacher` из
  `lesson_slot_teacher` на каждый тик — так же, как участников.
- `get_occurrence_for_teacher`/API `GET /teacher/lesson-occurrences`:
  со-преподаватель видит ТО ЖЕ occurrence с ТЕМ ЖЕ списком участников, что
  и основной — явка НЕ дублируется и НЕ требует синхронизации, потому что
  occurrence физически один.
- Cron no-show уведомляет ВСЕХ преподавателей occurrence, не только основного.
- `list_teachers_for_time`/`GET /me/teachers?at=`: реальный кейс — Денис
  Ильин привязан к 4 преподавателям (`student_teacher_links`), но на Пн
  17:00 слот есть только у одного, система не должна спрашивать выбор.
  Один слот с несколькими со-преподавателями — тоже без выбора (это одно
  занятие); выбор нужен, только если время покрывают ДВА РАЗНЫХ слота.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_student import LessonSlotStudent
from app.models.users import Users
from app.services import lesson_calendar_service
from app.services.auth.session_service import create_session
from app.services.lesson_attendance_cron_service import lesson_attendance_cron_tick
from app.services.lesson_occurrence_generator_service import lesson_occurrence_generator_tick
from app.services.lesson_occurrence_service import get_occurrence_for_teacher
from app.utils.exceptions import DomainError


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk443") -> int:
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
        teacher_id=teacher_id, weekday=weekday, start_time=start_time,
        duration_minutes=duration_minutes, timezone="Europe/Moscow", is_active=True,
    )
    db.add(slot)
    await db.flush()
    for student_id in student_ids:
        db.add(LessonSlotStudent(slot_id=slot.id, student_id=student_id, is_active=True))
    slot_id = slot.id
    await db.commit()
    return slot_id


@pytest.mark.asyncio
async def test_create_lesson_slot_adds_primary_teacher_to_m2m(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443-primary")
    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_id, weekday=0, start_time=time(9, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_id,
    )
    teachers = await lesson_calendar_service.list_slot_teachers(db, slot.id)
    assert [t.teacher_id for t in teachers] == [teacher_id]


@pytest.mark.asyncio
async def test_add_slot_teacher_idempotent(db):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-a")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-b")
    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=1, start_time=time(10, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
    )

    row1 = await lesson_calendar_service.add_slot_teacher(
        db, slot.id, teacher_b, added_by=teacher_a,
    )
    row2 = await lesson_calendar_service.add_slot_teacher(
        db, slot.id, teacher_b, added_by=teacher_a,
    )
    assert row1.id == row2.id

    teachers = await lesson_calendar_service.list_slot_teachers(db, slot.id)
    assert {t.teacher_id for t in teachers} == {teacher_a, teacher_b}


@pytest.mark.asyncio
async def test_add_slot_teacher_backfills_future_occurrence(db, db_session_factory):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-bfa")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-bfb")
    student_id = await _create_user(db, role="student", prefix="tsk443-bfs")

    slot_id = await _create_slot_with_students(
        db, teacher_id=teacher_a, student_ids=[student_id],
        weekday=date.today().weekday(), start_time=time(23, 59),
    )
    # Сначала добавим основного преподавателя в M2M вручную (create_lesson_slot
    # не вызывался — слот заведён напрямую через ORM, как в старых фикстурах).
    await lesson_calendar_service.add_slot_teacher(db, slot_id, teacher_a, added_by=teacher_a)

    await lesson_occurrence_generator_tick(db_session_factory)

    occurrence_ids = (
        await db.execute(
            text("SELECT id FROM lesson_occurrence WHERE slot_id = :sid ORDER BY scheduled_at"),
            {"sid": slot_id},
        )
    ).scalars().all()
    assert occurrence_ids  # хотя бы одно уже сгенерировано в пределах горизонта

    # Со-преподаватель добавляется ПОСЛЕ того, как occurrence уже сгенерирован.
    await lesson_calendar_service.add_slot_teacher(db, slot_id, teacher_b, added_by=teacher_a)

    for occurrence_id in occurrence_ids:
        linked = (
            await db.execute(
                text(
                    "SELECT teacher_id FROM lesson_occurrence_teacher WHERE occurrence_id = :oid"
                ),
                {"oid": occurrence_id},
            )
        ).scalars().all()
        assert set(linked) == {teacher_a, teacher_b}


@pytest.mark.asyncio
async def test_remove_slot_teacher_soft_delete_and_404_on_repeat(db):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-ra")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-rb")
    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=2, start_time=time(11, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
    )
    await lesson_calendar_service.add_slot_teacher(db, slot.id, teacher_b, added_by=teacher_a)

    await lesson_calendar_service.remove_slot_teacher(db, slot.id, teacher_b)
    teachers = await lesson_calendar_service.list_slot_teachers(db, slot.id)
    assert teacher_b not in {t.teacher_id for t in teachers}

    with pytest.raises(DomainError) as exc_info:
        await lesson_calendar_service.remove_slot_teacher(db, slot.id, teacher_b)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_has_overlap_catches_co_teacher_conflict(db):
    """Со-преподаватель уже ведёт другой слот в это же время — has_overlap
    должен поймать конфликт, даже если он не "основной" ни на одном из них."""
    teacher_owner1 = await _create_user(db, role="teacher", prefix="tsk443-o1")
    teacher_owner2 = await _create_user(db, role="teacher", prefix="tsk443-o2")
    co_teacher = await _create_user(db, role="teacher", prefix="tsk443-co")

    slot1 = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_owner1, weekday=3, start_time=time(12, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_owner1,
    )
    await lesson_calendar_service.add_slot_teacher(db, slot1.id, co_teacher, added_by=teacher_owner1)

    # Второй слот другого владельца в ТО ЖЕ время — если бы co_teacher стал
    # его со-преподавателем, конфликт должен быть пойман ДО добавления, на
    # этапе создания нового отдельного слота ДЛЯ co_teacher как владельца.
    with pytest.raises(DomainError) as exc_info:
        await lesson_calendar_service.create_lesson_slot(
            db, teacher_id=co_teacher, weekday=3, start_time=time(12, 0),
            duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_owner2,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_co_teacher_sees_same_occurrence_and_participants(db, client, db_session_factory):
    """API-уровень: со-преподаватель через GET /teacher/lesson-occurrences
    видит ТО ЖЕ занятие с ТЕМИ ЖЕ участниками, что и основной преподаватель."""
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-apia")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-apib")
    student_id = await _create_user(db, role="student", prefix="tsk443-apis")

    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=date.today().weekday(), start_time=time(23, 59),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
        student_ids=[student_id],
    )
    await lesson_calendar_service.add_slot_teacher(db, slot.id, teacher_b, added_by=teacher_a)
    await lesson_occurrence_generator_tick(db_session_factory)

    token_a, _, _ = await create_session(db, user_id=teacher_a)
    token_b, _, _ = await create_session(db, user_id=teacher_b)

    resp_a = await client.get(
        "/api/v1/teacher/lesson-occurrences",
        params={"teacher_id": teacher_a},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    resp_b = await client.get(
        "/api/v1/teacher/lesson-occurrences",
        params={"teacher_id": teacher_b},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    items_a = [i for i in resp_a.json() if i["id"]]
    items_b = [i for i in resp_b.json() if i["id"]]
    assert len(items_a) >= 1 and len(items_b) >= 1
    occurrence_a = items_a[0]
    occurrence_b = next(i for i in items_b if i["id"] == occurrence_a["id"])

    assert occurrence_a["id"] == occurrence_b["id"]  # физически ОДНО занятие
    assert {p["student_id"] for p in occurrence_a["participants"]} == {student_id}
    assert {p["student_id"] for p in occurrence_b["participants"]} == {student_id}


@pytest.mark.asyncio
async def test_co_teacher_can_mark_attendance_get_occurrence_for_teacher(db):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-owna")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-cob")
    unrelated_teacher = await _create_user(db, role="teacher", prefix="tsk443-unrel")

    from app.models.lesson_occurrence import LessonOccurrence

    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_a,
        scheduled_at=datetime.now(dt_timezone.utc) + timedelta(hours=1), duration_minutes=60,
    )
    db.add(occ)
    await db.flush()
    from app.repos.lesson_calendar_repository import LessonOccurrenceTeacherRepository

    await LessonOccurrenceTeacherRepository().create(
        db, occurrence_id=occ.id, teacher_id=teacher_b,
    )
    await db.commit()

    fetched = await get_occurrence_for_teacher(db, occurrence_id=occ.id, teacher_id=teacher_b)
    assert fetched.id == occ.id

    with pytest.raises(DomainError) as exc_info:
        await get_occurrence_for_teacher(db, occurrence_id=occ.id, teacher_id=unrelated_teacher)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_no_show_cron_notifies_all_co_teachers(db, db_session_factory):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-nsa")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-nsb")
    student_id = await _create_user(db, role="student", prefix="tsk443-nss")

    from app.models.lesson_occurrence import LessonOccurrence
    from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
    from app.repos.lesson_calendar_repository import LessonOccurrenceTeacherRepository

    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_a,
        scheduled_at=datetime.now(dt_timezone.utc) - timedelta(minutes=30), duration_minutes=15,
    )
    db.add(occ)
    await db.flush()
    await LessonOccurrenceTeacherRepository().create(db, occurrence_id=occ.id, teacher_id=teacher_a)
    await LessonOccurrenceTeacherRepository().create(db, occurrence_id=occ.id, teacher_id=teacher_b)
    db.add(LessonOccurrenceParticipant(occurrence_id=occ.id, student_id=student_id, status="scheduled"))
    await db.commit()

    summary = await lesson_attendance_cron_tick(db_session_factory)
    assert summary["no_show_marked"] >= 1

    notified_teachers = (
        await db.execute(
            text(
                "SELECT user_id FROM notifications WHERE kind = 'lesson_missed' "
                "AND (payload->>'occurrence_id')::int = :oid AND payload->>'role' = 'teacher'"
            ),
            {"oid": occ.id},
        )
    ).scalars().all()
    assert set(notified_teachers) == {teacher_a, teacher_b}


def _local_time_today_as_utc(local_time: time) -> datetime:
    """Сегодняшняя дата + указанное локальное время (Europe/Moscow) → UTC.
    weekday слота тоже берём как `date.today().weekday()` — согласовано."""
    tz = ZoneInfo("Europe/Moscow")
    local_dt = datetime.combine(date.today(), local_time, tzinfo=tz)
    return local_dt.astimezone(dt_timezone.utc)


async def _link_student_teacher(db, *, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t)"),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_list_teachers_for_time_single_slot_single_teacher(db):
    teacher_id = await _create_user(db, role="teacher", prefix="tsk443-lt1")
    await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_id, weekday=date.today().weekday(), start_time=time(17, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_id,
    )

    result = await lesson_calendar_service.list_teachers_for_time(
        db, scheduled_at=_local_time_today_as_utc(time(17, 0)),
    )
    assert [t.id for t in result] == [teacher_id]


@pytest.mark.asyncio
async def test_list_teachers_for_time_co_taught_slot_returns_single_representative(db):
    """Один слот с 3 со-преподавателями — выбор всё равно не нужен (одно занятие)."""
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-ltca")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-ltcb")
    teacher_c = await _create_user(db, role="teacher", prefix="tsk443-ltcc")
    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=date.today().weekday(), start_time=time(11, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
    )
    await lesson_calendar_service.add_slot_teacher(db, slot.id, teacher_b, added_by=teacher_a)
    await lesson_calendar_service.add_slot_teacher(db, slot.id, teacher_c, added_by=teacher_a)

    result = await lesson_calendar_service.list_teachers_for_time(
        db, scheduled_at=_local_time_today_as_utc(time(11, 0)),
    )
    assert len(result) == 1  # НЕ 3 — один представитель на слот
    assert result[0].id == teacher_a  # основной (slot.teacher_id)


@pytest.mark.asyncio
async def test_list_teachers_for_time_two_independent_slots_returns_both(db):
    """Два РАЗНЫХ (не пересекающихся по преподавателям) слота на один час —
    здесь выбор действительно нужен."""
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-lt2a")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-lt2b")
    weekday = date.today().weekday()
    await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=weekday, start_time=time(13, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
    )
    await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_b, weekday=weekday, start_time=time(13, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_b,
    )

    result = await lesson_calendar_service.list_teachers_for_time(
        db, scheduled_at=_local_time_today_as_utc(time(13, 0)),
    )
    assert {t.id for t in result} == {teacher_a, teacher_b}


@pytest.mark.asyncio
async def test_list_teachers_for_time_no_match_returns_empty(db):
    result = await lesson_calendar_service.list_teachers_for_time(
        db, scheduled_at=_local_time_today_as_utc(time(3, 30)),
    )
    assert result == []


@pytest.mark.asyncio
async def test_me_teachers_at_restricts_to_single_slot_teacher(db, client):
    """Реальный кейс (Денис Ильин): ученик привязан к 4 преподавателям, но
    на этот конкретный час слот есть только у одного — API должен вернуть
    только его, не все 4."""
    teacher_with_slot = await _create_user(db, role="teacher", prefix="tsk443-denis-a")
    other_teacher_1 = await _create_user(db, role="teacher", prefix="tsk443-denis-b")
    other_teacher_2 = await _create_user(db, role="teacher", prefix="tsk443-denis-c")
    student_id = await _create_user(db, role="student", prefix="tsk443-denis")

    await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_with_slot, weekday=date.today().weekday(), start_time=time(17, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_with_slot,
    )
    for t in (teacher_with_slot, other_teacher_1, other_teacher_2):
        await _link_student_teacher(db, student_id=student_id, teacher_id=t)

    token, _, _ = await create_session(db, user_id=student_id)
    at_iso = _local_time_today_as_utc(time(17, 0)).isoformat()

    resp = await client.get(
        "/api/v1/me/teachers", params={"at": at_iso},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert [t["id"] for t in resp.json()] == [teacher_with_slot]


@pytest.mark.asyncio
async def test_me_teachers_at_co_taught_slot_returns_one_not_all(db, client):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-coa")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-cob")
    student_id = await _create_user(db, role="student", prefix="tsk443-costud")

    slot = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_a, weekday=date.today().weekday(), start_time=time(9, 0),
        duration_minutes=60, timezone="Europe/Moscow", created_by=teacher_a,
    )
    await lesson_calendar_service.add_slot_teacher(db, slot.id, teacher_b, added_by=teacher_a)
    for t in (teacher_a, teacher_b):
        await _link_student_teacher(db, student_id=student_id, teacher_id=t)

    token, _, _ = await create_session(db, user_id=student_id)
    at_iso = _local_time_today_as_utc(time(9, 0)).isoformat()

    resp = await client.get(
        "/api/v1/me/teachers", params={"at": at_iso},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    ids = [t["id"] for t in resp.json()]
    assert len(ids) == 1
    assert ids[0] in (teacher_a, teacher_b)


@pytest.mark.asyncio
async def test_me_teachers_at_no_match_falls_back_to_full_list(db, client):
    teacher_a = await _create_user(db, role="teacher", prefix="tsk443-fba")
    teacher_b = await _create_user(db, role="teacher", prefix="tsk443-fbb")
    student_id = await _create_user(db, role="student", prefix="tsk443-fbstud")
    for t in (teacher_a, teacher_b):
        await _link_student_teacher(db, student_id=student_id, teacher_id=t)

    token, _, _ = await create_session(db, user_id=student_id)
    at_iso = _local_time_today_as_utc(time(3, 30)).isoformat()  # заведомо без слота

    resp = await client.get(
        "/api/v1/me/teachers", params={"at": at_iso},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert {t["id"] for t in resp.json()} == {teacher_a, teacher_b}
