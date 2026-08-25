"""tsk-679: слот действует по дату — старая сетка заканчивается 31 августа.

Покрывает то, из-за чего «убрать старые слоты» может не сработать:

- генератор не создаёт занятия за датой окончания (иначе календарь ученика
  снова наполнится сентябрём: горизонт генератора — две недели вперёд);
- установка даты убирает уже созданные занятия за ней, а прошедшие не трогает;
- перенос занятия не принимает время слота после даты (приём обязан быть не
  мягче выдачи — иначе ученик переедет в старое расписание в обход списка);
- деньги: месяц не считает дни после окончания слота, частота занятий в неделю
  тоже — иначе человеку выставят занятия, которых физически не будет;
- массовое завершение: `dry_run` ничего не меняет и показывает, сколько занятий
  уйдёт и скольких учеников это касается;
- гейт: ученику завершение расписания недоступно.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_slot import LessonSlot
from app.models.users import Users
from app.services import lesson_calendar_service
from app.services.auth.session_service import create_session
from app.services.lesson_occurrence_generator_service import iter_occurrence_datetimes

MOSCOW = "Europe/Moscow"


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk679") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
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
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


# ============================== Генератор ==============================


def test_generator_stops_at_active_until():
    """За последним днём действия занятий больше нет.

    Горизонт генератора — две недели, поэтому без этой проверки сентябрьские
    занятия старого расписания появлялись бы в базе уже в августе.
    """
    now = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    slot = LessonSlot(
        teacher_id=1, weekday=0, start_time=time(hour=17),
        duration_minutes=60, timezone=MOSCOW, is_active=True,
    )
    slot.active_until = date(2026, 8, 31)

    moments = iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now)
    days = {m.astimezone().date() for m in moments}

    assert moments, "занятия до даты окончания остаются"
    assert all(m.astimezone(tz=None).date() <= date(2026, 9, 1) for m in moments)
    assert not any(d > date(2026, 8, 31) for d in {
        m.date() for m in (x.astimezone() for x in moments)
    }), f"после 31 августа занятий быть не должно: {sorted(days)}"


def test_generator_keeps_last_day_itself():
    """31 августа — последний рабочий день, а не первый выходной."""
    now = datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc)
    slot = LessonSlot(
        teacher_id=1, weekday=0, start_time=time(hour=17),
        duration_minutes=60, timezone=MOSCOW, is_active=True,
    )
    slot.active_until = date(2026, 8, 31)  # понедельник

    moments = iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now)

    assert len(moments) == 1
    assert moments[0].astimezone().date() == date(2026, 8, 31)


def test_generator_without_date_is_unbounded():
    """Слот без даты работает как раньше — весь горизонт."""
    now = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
    slot = LessonSlot(
        teacher_id=1, weekday=0, start_time=time(hour=17),
        duration_minutes=60, timezone=MOSCOW, is_active=True,
    )

    moments = iter_occurrence_datetimes(slot, horizon_days=14, now_utc=now)
    assert len(moments) == 2


# ============================== Уборка занятий ==============================


async def _make_slot_with_lessons(db, *, weekday: int, hour: int):
    """Слот с учеником и парой сгенерированных занятий: одно завтра, одно через месяц."""
    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=weekday,
        start_time=time(hour=hour),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        student_ids=[student_id],
    )
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    far = datetime.now(timezone.utc) + timedelta(days=30)
    for moment in (soon, far):
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, "
                "       duration_minutes) VALUES (:s, :t, :at, 60)"
            ),
            {"s": slot.id, "t": teacher_id, "at": moment},
        )
    await db.commit()
    return slot, student_id, soon, far


@pytest.mark.asyncio
async def test_setting_date_removes_lessons_after_it(db):
    """Занятия за датой окончания уходят: звать на них человека нельзя."""
    slot, _student_id, soon, far = await _make_slot_with_lessons(db, weekday=0, hour=17)
    last_day = (datetime.now(timezone.utc) + timedelta(days=7)).date()

    await lesson_calendar_service.update_lesson_slot(
        db, slot.id, active_until=last_day
    )

    remaining = (
        await db.execute(
            text("SELECT scheduled_at FROM lesson_occurrence WHERE slot_id = :s"),
            {"s": slot.id},
        )
    ).scalars().all()

    assert len(remaining) == 1, "дальнее занятие должно уйти, ближнее — остаться"
    assert remaining[0].date() == soon.date()


@pytest.mark.asyncio
async def test_past_lessons_are_never_touched(db):
    """Прошедшее — история явки, её не трогаем ни при каких датах."""
    teacher_id = await _create_user(db, role="teacher")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=2,
        start_time=time(hour=12),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
    )
    past = datetime.now(timezone.utc) - timedelta(days=30)
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, "
            "       duration_minutes) VALUES (:s, :t, :at, 60)"
        ),
        {"s": slot.id, "t": teacher_id, "at": past},
    )
    await db.commit()

    await lesson_calendar_service.update_lesson_slot(
        db, slot.id, active_until=(datetime.now(timezone.utc) - timedelta(days=60)).date()
    )

    left = (
        await db.execute(
            text("SELECT count(*) FROM lesson_occurrence WHERE slot_id = :s"),
            {"s": slot.id},
        )
    ).scalar_one()
    assert left == 1


@pytest.mark.asyncio
async def test_clear_active_until_makes_slot_unbounded_again(db):
    """Дату можно снять — слот снова бессрочный."""
    teacher_id = await _create_user(db, role="teacher")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=3,
        start_time=time(hour=15),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        active_until=date(2026, 8, 31),
    )
    assert slot.active_until == date(2026, 8, 31)

    updated = await lesson_calendar_service.update_lesson_slot(
        db, slot.id, clear_active_until=True
    )
    assert updated.active_until is None


# ============================== Массовое завершение ==============================


@pytest.mark.asyncio
async def test_end_slots_dry_run_changes_nothing(db):
    """Предпросмотр показывает цену, но календарь не трогает."""
    slot, student_id, _soon, _far = await _make_slot_with_lessons(db, weekday=1, hour=14)
    last_day = (datetime.now(timezone.utc) + timedelta(days=7)).date()

    result = await lesson_calendar_service.end_slots_on(
        db, last_day=last_day, teacher_id=slot.teacher_id, dry_run=True
    )

    assert result["dry_run"] is True
    assert result["slots_total"] == 1
    assert result["occurrences_removed"] == 1
    assert student_id in result["students_affected"]

    still_there = (
        await db.execute(
            text("SELECT count(*) FROM lesson_occurrence WHERE slot_id = :s"),
            {"s": slot.id},
        )
    ).scalar_one()
    assert still_there == 2

    fresh = await lesson_calendar_service.get_lesson_slot(db, slot.id)
    assert fresh.active_until is None


@pytest.mark.asyncio
async def test_end_slots_sets_date_and_cleans_calendar(db):
    """Настоящее завершение: дата проставлена, лишние занятия убраны."""
    slot, _student_id, soon, _far = await _make_slot_with_lessons(db, weekday=4, hour=13)
    last_day = (datetime.now(timezone.utc) + timedelta(days=7)).date()

    result = await lesson_calendar_service.end_slots_on(
        db, last_day=last_day, teacher_id=slot.teacher_id, dry_run=False
    )

    assert result["dry_run"] is False
    fresh = await lesson_calendar_service.get_lesson_slot(db, slot.id)
    assert fresh.active_until == last_day

    remaining = (
        await db.execute(
            text("SELECT scheduled_at FROM lesson_occurrence WHERE slot_id = :s"),
            {"s": slot.id},
        )
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].date() == soon.date()


@pytest.mark.asyncio
async def test_end_slots_skips_already_ended(db):
    """Слот, уже закончившийся раньше названного дня, повторно не трогаем."""
    teacher_id = await _create_user(db, role="teacher")
    await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=5,
        start_time=time(hour=9),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        active_until=date(2026, 6, 30),
    )

    result = await lesson_calendar_service.end_slots_on(
        db, last_day=date(2026, 8, 31), teacher_id=teacher_id, dry_run=True
    )
    assert result["slots_total"] == 0


# ============================== Деньги ==============================


@pytest.mark.asyncio
async def test_month_count_stops_at_active_until(db):
    """Месяц не считает занятия после окончания слота — иначе счёт за воздух."""
    from app.services.charge_service import lesson_counts_for_period

    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=0,  # понедельник
        start_time=time(hour=17),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        student_ids=[student_id],
    )

    # Сентябрь 2026: понедельники 7, 14, 21, 28 — четыре занятия.
    full = await lesson_counts_for_period(
        db, student_id=student_id, period_from=date(2026, 9, 1), period_to=date(2026, 9, 30)
    )
    assert full.expected == 4

    await lesson_calendar_service.update_lesson_slot(
        db, slot.id, active_until=date(2026, 8, 31)
    )
    after = await lesson_counts_for_period(
        db, student_id=student_id, period_from=date(2026, 9, 1), period_to=date(2026, 9, 30)
    )
    assert after.expected == 0, "слот кончился в августе — в сентябре занятий нет"


@pytest.mark.asyncio
async def test_weekly_lessons_ignore_finished_slot(db):
    """«Сколько занятий в неделю» не считает уже закончившийся слот."""
    from app.services.pricing_service import _count_active_weekly_slots

    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=1,
        start_time=time(hour=16),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        student_ids=[student_id],
    )
    assert await _count_active_weekly_slots(db, student_id) == 1

    await lesson_calendar_service.update_lesson_slot(
        db, slot.id, active_until=date.today() - timedelta(days=1)
    )
    assert await _count_active_weekly_slots(db, student_id) == 0


# ============================== Перенос и гейт ==============================


@pytest.mark.asyncio
async def test_reschedule_rejects_time_after_slot_ends(db):
    """Приём переноса не мягче выдачи: время за датой окончания не подходит."""
    from app.services.lesson_occurrence_service import _slot_starts_at

    slot = LessonSlot(
        teacher_id=1, weekday=0, start_time=time(hour=17),
        duration_minutes=60, timezone=MOSCOW, is_active=True,
    )
    slot.active_until = date(2026, 8, 31)

    # Понедельник 31 августа 17:00 МСК — ещё можно.
    ok_moment = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    # Понедельник 7 сентября 17:00 МСК — уже нет.
    late_moment = datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc)

    assert _slot_starts_at(slot, ok_moment) is True
    assert _slot_starts_at(slot, late_moment) is False


@pytest.mark.asyncio
async def test_end_slots_endpoint_is_closed_for_students(db, client):
    """Завершение расписания — решение методиста, ученику недоступно."""
    student_id = await _create_user(db, role="student")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.post(
        "/api/v1/lesson-slots/end-on",
        json={"last_day": "2026-08-31", "dry_run": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_removing_lesson_takes_participants_with_it(db):
    """Занятие с записанными учениками удаляется без спотыкания о внешние ключи.

    На проде у каждого сгенерированного занятия есть участники, и если бы
    удаление падало по внешнему ключу, завершение расписания сломалось бы
    ровно там, где оно нужно.
    """
    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")
    slot = await lesson_calendar_service.create_lesson_slot(
        db,
        teacher_id=teacher_id,
        weekday=0,
        start_time=time(hour=18),
        duration_minutes=60,
        timezone=MOSCOW,
        created_by=None,
        student_ids=[student_id],
    )
    far = datetime.now(timezone.utc) + timedelta(days=30)
    occurrence_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, "
                "       duration_minutes) VALUES (:s, :t, :at, 60) RETURNING id"
            ),
            {"s": slot.id, "t": teacher_id, "at": far},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, 'scheduled')"
        ),
        {"o": occurrence_id, "s": student_id},
    )
    await db.commit()

    await lesson_calendar_service.end_slots_on(
        db,
        last_day=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
        teacher_id=teacher_id,
        dry_run=False,
    )

    left_occurrences = (
        await db.execute(
            text("SELECT count(*) FROM lesson_occurrence WHERE id = :o"),
            {"o": occurrence_id},
        )
    ).scalar_one()
    left_participants = (
        await db.execute(
            text(
                "SELECT count(*) FROM lesson_occurrence_participant "
                " WHERE occurrence_id = :o"
            ),
            {"o": occurrence_id},
        )
    ).scalar_one()
    assert left_occurrences == 0
    assert left_participants == 0


@pytest.mark.asyncio
async def test_schedule_ends_on_only_when_all_slots_end(db):
    """Дата окончания расписания — только когда кончаются ВСЕ слоты ученика.

    Иначе кабинет сказал бы «занятия заканчиваются 31 августа» человеку, у
    которого половина расписания продолжается.
    """
    from app.services import schedule_preference_service

    teacher_id = await _create_user(db, role="teacher")
    student_id = await _create_user(db, role="student")
    first = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_id, weekday=0, start_time=time(hour=12),
        duration_minutes=60, timezone=MOSCOW, created_by=None,
        student_ids=[student_id],
    )
    second = await lesson_calendar_service.create_lesson_slot(
        db, teacher_id=teacher_id, weekday=2, start_time=time(hour=12),
        duration_minutes=60, timezone=MOSCOW, created_by=None,
        student_ids=[student_id],
    )

    assert await schedule_preference_service.schedule_ends_on(db, student_id) is None

    await lesson_calendar_service.update_lesson_slot(
        db, first.id, active_until=date(2026, 8, 31)
    )
    # Второй слот ещё бессрочный — расписание не кончается.
    assert await schedule_preference_service.schedule_ends_on(db, student_id) is None

    await lesson_calendar_service.update_lesson_slot(
        db, second.id, active_until=date(2026, 9, 30)
    )
    # Кончаются оба — берём последний день из них.
    assert await schedule_preference_service.schedule_ends_on(db, student_id) == date(2026, 9, 30)


@pytest.mark.asyncio
async def test_schedule_ends_on_is_none_without_slots(db):
    """У ученика без расписания заканчиваться нечему."""
    from app.services import schedule_preference_service

    student_id = await _create_user(db, role="student")
    assert await schedule_preference_service.schedule_ends_on(db, student_id) is None
