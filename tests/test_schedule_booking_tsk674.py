"""tsk-674 фаза 3: запись нового ученика в свободные слоты.

Покрывает то, из-за чего запись может обмануть человека:

- слот, набравший потолок (10), не показывается вовсе — а не показывается серым;
- свободный (меньше 5) и частично свободный различаются;
- слоты старой сетки (утро, дата окончания 30 августа) в предложение не идут;
- часы, которые ученик назвал желательными, стоят первыми;
- записаться можно только в показанное: набравшийся слот отдаёт понятный отказ;
- ученик не встаёт в большее число занятий, чем сам просил;
- без заполненных пожеланий запись закрыта;
- кнопка «не нашёл время» кладёт заявку и уведомляет методистов, повторное
  нажатие не плодит вторую;
- запись сама закрывает открытую заявку;
- очередь заявок закрыта для ученика.
"""
from __future__ import annotations

import random
from datetime import date, time, timedelta

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.schemas.schedule_preference import SchedulePreferenceWrite
from app.services import schedule_booking_service, schedule_preference_service
from app.services.auth.session_service import create_session
from app.utils.exceptions import DomainError


async def _create_user(db, *, role: str | None = None, prefix: str = "tsk674b") -> int:
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


async def _create_slot(
    db,
    teacher_id: int,
    *,
    weekday: int,
    hour: int,
    students: list[int] | None = None,
    active_until: date | None = None,
) -> int:
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot "
                "       (teacher_id, weekday, start_time, duration_minutes, timezone, "
                "        is_active, active_until) "
                "VALUES (:t, :w, :st, 60, 'Europe/Moscow', true, :au) RETURNING id"
            ),
            {"t": teacher_id, "w": weekday, "st": time(hour=hour), "au": active_until},
        )
    ).scalar_one()
    for sid in students or []:
        await db.execute(
            text(
                "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
                "VALUES (:s, :u, true)"
            ),
            {"s": slot_id, "u": sid},
        )
    await db.commit()
    return int(slot_id)


async def _fill_preference(db, student_id: int, *, lessons: int = 1, hours=()) -> None:
    await schedule_preference_service.save_preference(
        db,
        student_id,
        SchedulePreferenceWrite(
            lessons_per_week=lessons,
            hours=[
                {"weekday": w, "start_time": f"{h:02d}:00", "kind": kind}
                for w, h, kind in hours
            ],
        ),
        changed_by=student_id,
    )


# ───────────────────────────── что видно ученику ────────────────────────────


def test_slot_is_alive_ignores_slot_that_dies_before_next_lesson():
    """Слот старой сетки, доживающий до 30 августа, новому ученику не годится.

    Он ещё активен, но занятие в нём уже не состоится, а запись означала бы
    обещание, которого школа не выполнит (tsk-679 пометил летнюю сетку датой).
    """
    monday = date(2026, 8, 31)  # понедельник
    # Слот в среду, действует по 30 августа: ближайшая среда — 2 сентября.
    assert schedule_booking_service.slot_is_alive(2, date(2026, 8, 30), monday) is False
    # Бессрочный живёт всегда.
    assert schedule_booking_service.slot_is_alive(2, None, monday) is True
    # Действует ещё неделю — годится.
    assert schedule_booking_service.slot_is_alive(2, date(2026, 9, 10), monday) is True


@pytest.mark.asyncio
async def test_full_slot_is_not_offered_at_all(db):
    """Слот с десятью учениками не показывается: это запрет оператора."""
    teacher_id = await _create_user(db, role="teacher")
    crowd = [await _create_user(db, role="student") for _ in range(10)]
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])

    full_id = await _create_slot(db, teacher_id, weekday=0, hour=12, students=crowd)
    free_id = await _create_slot(db, teacher_id, weekday=0, hour=13)

    data = await schedule_booking_service.get_bookable(db, newcomer)
    offered = {s.slot_id for s in data["slots"]}

    assert full_id not in offered, "набравшийся слот не предлагается вовсе"
    assert free_id in offered


@pytest.mark.asyncio
async def test_free_and_partial_are_distinguished(db):
    """Меньше цели — свободный; цель набрана, но места есть — частично свободный."""
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(1, 12, "preferred")])

    few = [await _create_user(db, role="student") for _ in range(2)]
    many = [await _create_user(db, role="student") for _ in range(7)]
    free_id = await _create_slot(db, teacher_id, weekday=1, hour=12, students=few)
    partial_id = await _create_slot(db, teacher_id, weekday=1, hour=13, students=many)

    data = await schedule_booking_service.get_bookable(db, newcomer)
    by_id = {s.slot_id: s for s in data["slots"]}

    assert by_id[free_id].availability == "free"
    assert by_id[partial_id].availability == "partial"
    assert by_id[partial_id].seats_left == 3


@pytest.mark.asyncio
async def test_preferred_hours_come_first(db):
    """Час, который ученик сам назвал желательным, стоит выше остальных.

    Иначе опрос, который человек уже заполнил, ничего для него не меняет —
    он снова выбирает вслепую из двух десятков одинаковых кнопок.
    """
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(
        db, newcomer, hours=[(3, 18, "preferred"), (3, 15, "possible")]
    )

    await _create_slot(db, teacher_id, weekday=3, hour=12)  # никто не просил
    possible_id = await _create_slot(db, teacher_id, weekday=3, hour=15)
    preferred_id = await _create_slot(db, teacher_id, weekday=3, hour=18)

    data = await schedule_booking_service.get_bookable(db, newcomer)
    order = [s.slot_id for s in data["slots"]]

    assert order.index(preferred_id) < order.index(possible_id)
    assert order[0] == preferred_id


@pytest.mark.asyncio
async def test_old_morning_slot_is_not_offered(db):
    """Утренний слот старой сетки в предложение не попадает: осенью его нет."""
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])

    morning_id = await _create_slot(db, teacher_id, weekday=0, hour=10)

    data = await schedule_booking_service.get_bookable(db, newcomer)
    assert morning_id not in {s.slot_id for s in data["slots"]}


# ─────────────────────────────── сама запись ────────────────────────────────


@pytest.mark.asyncio
async def test_join_puts_student_into_slot(db):
    """Запись доводит ученика до слота тем же путём, что и рука методиста."""
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(2, 16, "preferred")])
    slot_id = await _create_slot(db, teacher_id, weekday=2, hour=16)

    data = await schedule_booking_service.join_slot(db, newcomer, slot_id)

    members = (
        await db.execute(
            text(
                "SELECT student_id FROM lesson_slot_student "
                " WHERE slot_id = :s AND is_active"
            ),
            {"s": slot_id},
        )
    ).scalars().all()
    assert members == [newcomer]
    assert data["booked_count"] == 1
    assert data["can_book_more"] is False
    assert slot_id in {s.slot_id for s in data["my_slots"]}


@pytest.mark.asyncio
async def test_join_rejects_full_slot(db):
    """В набравшийся слот записаться нельзя — даже если экран успел устареть."""
    teacher_id = await _create_user(db, role="teacher")
    crowd = [await _create_user(db, role="student") for _ in range(10)]
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 14, "preferred")])
    slot_id = await _create_slot(db, teacher_id, weekday=0, hour=14, students=crowd)

    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.join_slot(db, newcomer, slot_id)
    assert "набралась" in str(exc.value)


@pytest.mark.asyncio
async def test_join_respects_lessons_per_week(db):
    """Больше занятий, чем ученик сам просил, он себе не назначит.

    За каждое занятие идёт начисление, и лишний клик превращается в счёт.
    Захотел больше — сначала правит пожелания (решение оператора 2026-08-27).
    """
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(
        db, newcomer, lessons=1, hours=[(0, 12, "preferred"), (1, 12, "possible")]
    )
    first = await _create_slot(db, teacher_id, weekday=0, hour=12)
    second = await _create_slot(db, teacher_id, weekday=1, hour=12)

    await schedule_booking_service.join_slot(db, newcomer, first)
    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.join_slot(db, newcomer, second)
    assert "измените пожелания" in str(exc.value)


@pytest.mark.asyncio
async def test_join_twice_is_rejected(db):
    """Повторная запись в тот же слот отклоняется до всех прочих проверок.

    Двойной клик — самая частая причина повтора, и он не должен превращаться
    ни в дубль участника, ни в невнятную ошибку про предел занятий.
    """
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, lessons=2, hours=[(0, 12, "preferred"), (1, 12, "preferred")])
    slot_id = await _create_slot(db, teacher_id, weekday=0, hour=12)

    await schedule_booking_service.join_slot(db, newcomer, slot_id)
    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.join_slot(db, newcomer, slot_id)
    assert "уже занимаетесь" in str(exc.value)


@pytest.mark.asyncio
async def test_join_requires_filled_preference(db):
    """Без ответов на опрос запись закрыта: подбирать время не по чему."""
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    slot_id = await _create_slot(db, teacher_id, weekday=0, hour=12)

    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.join_slot(db, newcomer, slot_id)
    assert "когда вам удобно" in str(exc.value)


@pytest.mark.asyncio
async def test_join_rejects_dead_slot(db):
    """Слот, который не доживёт до ближайшего занятия, не принимает запись."""
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])
    dead_id = await _create_slot(
        db, teacher_id, weekday=0, hour=12,
        active_until=date.today() - timedelta(days=1),
    )

    with pytest.raises(DomainError) as exc:
        await schedule_booking_service.join_slot(db, newcomer, dead_id)
    assert "не действует" in str(exc.value)


# ─────────────────────── «не нашёл подходящее время» ────────────────────────


@pytest.mark.asyncio
async def test_request_reaches_methodist(db):
    """Заявка ложится в очередь и уходит методисту уведомлением.

    Уведомления мало: в этой школе сигнал уже дважды оставался непрочитанным
    (tsk-591, tsk-652), поэтому у заявки есть собственный статус и очередь.
    """
    methodist_id = await _create_user(db, role="methodist")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])

    request = await schedule_booking_service.create_request(
        db, newcomer, "после 18 не успеваю с тренировки"
    )

    assert request.status == "open"
    assert request.comment == "после 18 не успеваю с тренировки"

    notif = (
        await db.execute(
            text(
                "SELECT title, payload FROM notifications "
                " WHERE user_id = :m AND kind = :k ORDER BY id DESC LIMIT 1"
            ),
            {"m": methodist_id, "k": schedule_booking_service.REQUEST_KIND},
        )
    ).first()
    assert notif is not None, "методист должен получить уведомление"
    assert notif[1]["comment"] == "после 18 не успеваю с тренировки"
    assert notif[1]["preferred"] == ["пн 12:00"]

    queue = await schedule_booking_service.list_requests(db, status="open")
    assert newcomer in {item.student_id for item in queue["items"]}


@pytest.mark.asyncio
async def test_second_request_updates_the_same_one(db):
    """Повторное нажатие не плодит вторую заявку — обновляет открытую."""
    await _create_user(db, role="methodist")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])

    first = await schedule_booking_service.create_request(db, newcomer, "утром никак")
    second = await schedule_booking_service.create_request(db, newcomer, "и в субботу")

    assert first.id == second.id
    assert second.comment == "и в субботу"
    count = (
        await db.execute(
            text(
                "SELECT count(*) FROM schedule_slot_request "
                " WHERE student_id = :s AND status = 'open'"
            ),
            {"s": newcomer},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_join_closes_open_request(db):
    """Ученик нашёл время сам — заявка уходит из очереди методиста."""
    await _create_user(db, role="methodist")
    teacher_id = await _create_user(db, role="teacher")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 13, "preferred")])
    slot_id = await _create_slot(db, teacher_id, weekday=0, hour=13)

    await schedule_booking_service.create_request(db, newcomer, "ничего не подходит")
    await schedule_booking_service.join_slot(db, newcomer, slot_id)

    assert await schedule_booking_service.get_open_request(db, newcomer) is None


@pytest.mark.asyncio
async def test_resolve_marks_request_done(db):
    """Методист закрывает заявку, и она перестаёт считаться ждущей."""
    methodist_id = await _create_user(db, role="methodist")
    newcomer = await _create_user(db, role="student")
    await _fill_preference(db, newcomer, hours=[(0, 12, "preferred")])
    request = await schedule_booking_service.create_request(db, newcomer, None)

    resolved = await schedule_booking_service.resolve_request(
        db, request.id, resolution_note="добавили слот пн 19:00", resolved_by=methodist_id
    )

    assert resolved.status == "resolved"
    assert resolved.resolution_note == "добавили слот пн 19:00"
    queue = await schedule_booking_service.list_requests(db, status="open")
    assert newcomer not in {item.student_id for item in queue["items"]}


@pytest.mark.asyncio
async def test_requests_queue_is_closed_for_students(client, db):
    """Очередь заявок — экран методиста, ученику она недоступна."""
    student_id = await _create_user(db, role="student")
    token, _, _ = await create_session(db, user_id=student_id)

    resp = await client.get(
        "/api/v1/methodist/schedule-slot-requests",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
