"""tsk-996, группа 2: календарь, посещаемость, план и сводка занятия.

«Два занятия в одно время» здесь норма (группы, со-преподаватели, остатки
переезда), а равные времена дают и вставки одной транзакции (`now()` одинаков).
Каждый тест строит такую ничью, перекладывает строку в конец кучи и проверяет,
что под любым планом результат один и тот же — детерминированный по id.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceParticipantRepository,
    LessonOccurrenceRepository,
    LessonOccurrenceTeacherRepository,
    LessonSlotRepository,
)
from app.services import absence_poll_service, attendance_service, homework_service
from app.services import lesson_attendance_cron_service as cron
from app.services import lesson_plan_service, teacher_lesson_summary_service as summary
from app.services.learning_gaps_service import real_student_results_filter
from tests.plan_order import assert_same, move_to_heap_end, run_under_plans


async def _user(db: Any, role: Optional[str] = None) -> int:
    """Пользователь (и роль, если задана)."""
    u = Users(
        email=f"tsk996g2-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk996 g2", tg_id=None,
    )
    db.add(u)
    await db.flush()
    if role:
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE name = :r"
        ), {"u": u.id, "r": role})
    return int(u.id)


async def _occ(db: Any, teacher: int, at: datetime, duration: int = 60) -> int:
    return int((await db.execute(text(
        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
        "VALUES (:t, :at, :d) RETURNING id"
    ), {"t": teacher, "at": at, "d": duration})).scalar_one())


async def _part(db: Any, occ: int, student: int, status: str = "scheduled") -> int:
    return int((await db.execute(text(
        "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
        "VALUES (:o, :s, :st) RETURNING id"
    ), {"o": occ, "s": student, "st": status})).scalar_one())


def _past_hour(hours_ago: int) -> datetime:
    """Ровный час в прошлом (без секунд — чтобы ничьи были точными)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=hours_ago)


# ─── Репозиторий календаря ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slots_same_time_order_does_not_depend_on_plan(db):
    """Два слота преподавателя на одно время: `_find_slot_at` берёт первый."""
    teacher = await _user(db, "teacher")
    ids = [
        int((await db.execute(text(
            "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
            "VALUES (:t, 2, '17:00', 60) RETURNING id"
        ), {"t": teacher})).scalar_one())
        for _ in range(2)
    ]
    await move_to_heap_end(db, "lesson_slot", ids[0])
    await db.commit()
    repo = LessonSlotRepository()

    async def call() -> list[int]:
        return [s.id for s in await repo.list_active(db, teacher_id=teacher)]

    assert_same(await run_under_plans(db, call), ids)


@pytest.mark.asyncio
async def test_occurrence_teachers_order_does_not_depend_on_plan(db):
    """Порядок ведущих решает, к чьему занятию присоединят ученика при переносе."""
    owner, t1, t2 = await _user(db, "teacher"), await _user(db, "teacher"), await _user(db, "teacher")
    occ = await _occ(db, owner, _past_hour(-48))
    links = [
        int((await db.execute(text(
            "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id) "
            "VALUES (:o, :t) RETURNING id"
        ), {"o": occ, "t": t})).scalar_one())
        for t in (t1, t2)
    ]
    await move_to_heap_end(db, "lesson_occurrence_teacher", links[0])
    await db.commit()
    repo = LessonOccurrenceTeacherRepository()

    async def call() -> list[int]:
        return [link.teacher_id for link in await repo.list_for_occurrence(db, occ)]

    assert_same(await run_under_plans(db, call), [t1, t2])


@pytest.mark.asyncio
async def test_teacher_same_time_occurrence_does_not_depend_on_plan(db):
    """Два занятия преподавателя на одно время: `_find_occurrence_at` берёт первое."""
    teacher = await _user(db, "teacher")
    at = _past_hour(-72)
    ids = [await _occ(db, teacher, at) for _ in range(3)]
    await move_to_heap_end(db, "lesson_occurrence", ids[0])
    await db.commit()
    repo = LessonOccurrenceRepository()

    async def call() -> list[int]:
        rows = await repo.list_for_teacher(db, teacher_id=teacher, from_dt=at, to_dt=at, limit=5)
        return [o.id for o in rows]

    assert_same(await run_under_plans(db, call), ids)


@pytest.mark.asyncio
async def test_student_occurrences_same_time_order_does_not_depend_on_plan(db):
    """«Мои занятия» ученика: два занятия на одно время идут по id."""
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    at = _past_hour(-96)
    ids = [await _occ(db, teacher, at) for _ in range(3)]
    for occ in ids:
        await _part(db, occ, student)
    await move_to_heap_end(db, "lesson_occurrence", ids[0])
    await db.commit()
    repo = LessonOccurrenceParticipantRepository()

    async def call() -> list[int]:
        return [o.id for _p, o in await repo.list_for_student(db, student_id=student)]

    assert_same(await run_under_plans(db, call), ids)


@pytest.mark.asyncio
async def test_current_scheduled_participation_does_not_depend_on_plan(db):
    """Окна соседних занятий перекрылись запасом: явка — идущему (раннему).

    Следующее занятие заведено раньше (меньший id участия), поэтому чтение
    кучи без порядка отдало бы его.
    """
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    now = datetime.now(timezone.utc)
    nxt = await _occ(db, teacher, now + timedelta(minutes=3))
    cur = await _occ(db, teacher, now - timedelta(minutes=50))
    p_next = await _part(db, nxt, student)
    p_cur = await _part(db, cur, student)
    await move_to_heap_end(db, "lesson_occurrence_participant", p_cur)
    await db.commit()
    assert p_next < p_cur
    repo = LessonOccurrenceParticipantRepository()

    async def call() -> Optional[int]:
        row = await repo.get_current_scheduled_for_student(
            db, student_id=student, now=now, early_grace_minutes=5,
        )
        return row.id if row else None

    assert_same(await run_under_plans(db, call), p_cur)


# ─── Сервисы ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_absence_answer_binds_same_poll_under_any_plan(db):
    """Два опроса одного тика (равный asked_at): ответ — к свежему по id.

    Пара опросов создаётся заново под каждым планом: ответ сам пишет в
    строку и сдвигал бы её в куче для следующего плана.
    """
    teacher = await _user(db, "teacher")
    at = datetime.now(timezone.utc) - timedelta(minutes=10)
    reason_label = absence_poll_service.ANSWERS[
        next(code for code in absence_poll_service.ANSWERS if code != "late")
    ]

    async def call() -> list[int]:
        student = await _user(db, "student")
        # Больший id ложится в кучу и индекс первым: правка строки здесь не
        # помогла бы — она HOT, и порядок в индексе (student_id, asked_at)
        # остаётся прежним.
        polls = sorted([
            int((await db.execute(text("SELECT nextval('absence_poll_id_seq')"))).scalar_one())
            for _ in range(2)
        ])
        for poll_id in reversed(polls):
            occ = await _occ(db, teacher, at)
            part = await _part(db, occ, student)
            await db.execute(text(
                "INSERT INTO absence_poll (id, participant_id, occurrence_id, student_id) "
                "VALUES (:id, :p, :o, :s)"
            ), {"id": poll_id, "p": part, "o": occ, "s": student})
        await absence_poll_service.handle_answer(
            db, student_id=student, channel="max", body=reason_label,
        )
        answered = {int(r[0]) for r in (await db.execute(text(
            "SELECT id FROM absence_poll WHERE id = ANY(:i) AND answer IS NOT NULL"
        ), {"i": polls})).all()}
        return [i for i, poll in enumerate(polls) if poll in answered]

    assert_same(await run_under_plans(db, call), [1])


@pytest.mark.asyncio
async def test_attendance_outcomes_order_does_not_depend_on_plan(db):
    """Два пропуска в одно время: порядок занятий (серия считается с конца) — по id."""
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    at = _past_hour(30)
    ids = [await _occ(db, teacher, at) for _ in range(3)]
    for occ in ids:
        await _part(db, occ, student, "no_show")
    await move_to_heap_end(db, "lesson_occurrence", ids[0])
    await db.commit()

    async def call() -> list[int]:
        rows = await attendance_service.outcomes(
            db, student_id=student, since=at - timedelta(days=1), until=at + timedelta(days=1),
        )
        return [o.occurrence_id for o in rows]

    assert_same(await run_under_plans(db, call), ids)


@pytest.mark.asyncio
async def test_homework_after_lessons_chronological_under_any_plan(db, monkeypatch):
    """Два закончившихся занятия: первым обрабатывается раннее — оно и выдаёт ДЗ.

    Участие в позднем занятии вставлено раньше: в куче оно идёт первым.
    """
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    early = await _occ(db, teacher, datetime.now(timezone.utc) - timedelta(hours=4))
    late = await _occ(db, teacher, datetime.now(timezone.utc) - timedelta(hours=2))
    await _part(db, late, student, "confirmed")
    await _part(db, early, student, "confirmed")
    await db.commit()
    calls: list[int] = []

    async def fake_issue(db_: Any, *, student_id: int, occurrence_id: int, **_kw: Any) -> None:
        if student_id == student:
            calls.append(occurrence_id)
        return None

    monkeypatch.setattr(homework_service, "auto_issue_after_lesson", fake_issue)

    async def call() -> list[int]:
        calls.clear()
        await cron._issue_homework_after_lessons(db)
        return list(calls)

    assert_same(await run_under_plans(db, call), [early, late])


@pytest.mark.asyncio
async def test_lesson_plan_achievement_same_moment_under_any_plan(db):
    """Две вехи одного прохода (равный earned_at): хвалим за одну и ту же."""
    student = await _user(db, "student")
    ach = [
        int((await db.execute(text(
            "INSERT INTO achievements (name, condition) VALUES (:n, '{}'::jsonb) RETURNING id"
        ), {"n": f"tsk996g2 веха {i} {random.randint(1, 10**9)}"})).scalar_one())
        for i in range(2)
    ]
    for a in reversed(ach):
        await db.execute(text(
            "INSERT INTO user_achievements (user_id, achievement_id) VALUES (:u, :a)"
        ), {"u": student, "a": a})
    await move_to_heap_end(db, "user_achievements", ach[0], column="achievement_id")
    await db.commit()
    name = (await db.execute(text("SELECT name FROM achievements WHERE id = :a"), {"a": ach[1]})).scalar_one()

    async def call() -> Optional[str]:
        res = await lesson_plan_service._load_recent_achievements(
            db, student_ids=[student], now=datetime.now(timezone.utc),
        )
        return res.get(student)

    assert_same(await run_under_plans(db, call), name)


@pytest.mark.asyncio
async def test_prev_occurrence_same_time_under_any_plan(db):
    """Два занятия ученика в одно время: «предыдущим» считается одно — больший id."""
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    at = _past_hour(5)
    first = await _occ(db, teacher, at, duration=60)
    second = await _occ(db, teacher, at, duration=90)
    await _part(db, first, student, "confirmed")
    await _part(db, second, student, "confirmed")
    await move_to_heap_end(db, "lesson_occurrence", second)
    await db.commit()

    async def call() -> tuple[Any, ...]:
        window_from, _streak, prev_id, _at = await summary._load_prev_occurrence_and_streak(
            db, student_id=student, before=at + timedelta(hours=3),
        )
        return window_from, prev_id

    assert_same(await run_under_plans(db, call), (at + timedelta(minutes=90), second))


@pytest.mark.asyncio
async def test_ready_for_harder_first_try_under_any_plan(db):
    """Две сдачи с равным received_at: первой попыткой считается меньший id (неверная)."""
    student = await _user(db, "student")
    course = int((await db.execute(text(
        "INSERT INTO courses (title, access_level) VALUES ('tsk996g2 курс', 'self_guided') RETURNING id"
    ))).scalar_one())
    task = int((await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, max_score) "
        "VALUES ('{}'::jsonb, :c, 4, 10) RETURNING id"
    ), {"c": course})).scalar_one())
    results = [
        int((await db.execute(text(
            "INSERT INTO task_results (user_id, task_id, score, max_score, is_correct, source_system) "
            "VALUES (:u, :t, :sc, 10, :ok, 'spw_web') RETURNING id"
        ), {"u": student, "t": task, "sc": 10 if ok else 0, "ok": ok})).scalar_one())
        for ok in (False, True)
    ]
    await move_to_heap_end(db, "task_results", results[0])
    await db.commit()
    sql = text(summary._READY_FOR_HARDER_SQL.format(real_student=real_student_results_filter("tr")))

    async def call() -> list[tuple[int, int]]:
        rows = (await db.execute(sql, {
            "student_ids": [student], "days": 30,
            "difficulty_ids": list(summary._HARDER_DIFFICULTY_IDS),
        })).mappings().all()
        return [(int(r["tasks"]), int(r["first_try_ok"])) for r in rows]

    assert_same(await run_under_plans(db, call), [(1, 0)])
