"""tsk-1177: мягкий возврат затихших учеников.

По тесту на каждое решение:
- неделя без своих сдач и без входа при действующем расписании → напоминание;
- своя сдача или вход в кабинет за неделю снимают его; ручной зачёт — нет;
- выпускник (`alumni`) и тестовая учётка (`test`) не получают (решение оператора);
- перерыв и отсутствие расписания снимают;
- повторный проход в тот же день второй строки не пишет;
- эндпоинт для бота отдаёт только свой вид и только своему/сервису.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import reengage_nudge_service as nudge
from app.services.auth import identity_link_service

DAYS = 7
_settings = Settings()


def _service_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _user(db, prefix: str, *, role: str = "student") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_id) "
        "SELECT :u, id FROM roles WHERE name = :r ON CONFLICT DO NOTHING"
    ), {"u": u.id, "r": role})
    await db.commit()
    return u.id


async def _group_id(db) -> int:
    gid = (await db.execute(text("SELECT min(id) FROM schedule_group"))).scalar()
    if gid is None:
        gid = (await db.execute(text(
            "INSERT INTO schedule_group (audience, subject, name) "
            "VALUES ('kids', 'tsk1177', 'tsk1177') RETURNING id"
        ))).scalar_one()
        await db.commit()
    return int(gid)


async def _slot(db, *, teacher_id: int, student_id: int) -> int:
    slot_id = int((await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, group_id) "
        "VALUES (:t, 1, '15:00', 60, :g) RETURNING id"
    ), {"t": teacher_id, "g": await _group_id(db)})).scalar_one())
    await db.execute(text(
        "INSERT INTO lesson_slot_student (slot_id, student_id) VALUES (:s, :u)"
    ), {"s": slot_id, "u": student_id})
    await db.commit()
    return slot_id


async def _course(db) -> int:
    cid = int((await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES ('tsk1177', 'auto_check', false, :u) RETURNING id"
    ), {"u": f"tsk1177-{random.randint(10**8, 10**10)}"})).scalar_one())
    await db.commit()
    return cid


async def _submission(db, *, user_id: int, course_id: int, days_ago: int,
                      source: str = "spw_web") -> None:
    task_id = int((await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
        " course_id, difficulty_id, is_active) "
        "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1, true) RETURNING id"
    ), {
        "e": f"tsk1177-t-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA", "stem": "2+2"}),
        "r": json.dumps({"answers": ["4"]}),
        "cid": course_id,
    })).scalar_one())
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
        " received_at, max_score, source_system, is_correct, answer_json) "
        "VALUES (1, :u, :t, :w, 0, :w, 1, :src, true, CAST(:a AS jsonb))"
    ), {"u": user_id, "t": task_id, "w": when, "src": source,
        "a": json.dumps({"type": "SA", "response": {"value": "4"}})})
    await db.commit()


async def _presence(db, *, student_id: int, days_ago: int) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await db.execute(text(
        "INSERT INTO student_presence (student_id, last_seen_at, last_interaction_at) "
        "VALUES (:u, :w, :w) ON CONFLICT (student_id) DO UPDATE "
        "SET last_seen_at = :w, last_interaction_at = :w"
    ), {"u": student_id, "w": when})
    await db.commit()


async def _plan(db, *, student_id: int, code: str) -> None:
    await db.execute(text(
        "INSERT INTO student_subscription (student_id, plan_id) "
        "SELECT :u, id FROM subscription_plan WHERE code = :c"
    ), {"u": student_id, "c": code})
    await db.commit()


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.execute(text(
        "DELETE FROM task_results WHERE task_id IN "
        "(SELECT id FROM tasks WHERE course_id = ANY(:c))"
    ), {"c": courses})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": courses})
    for sql in (
        "DELETE FROM notifications WHERE user_id = ANY(:u)",
        "DELETE FROM student_presence WHERE student_id = ANY(:u)",
        "DELETE FROM student_break WHERE student_id = ANY(:u)",
        "DELETE FROM student_subscription WHERE student_id = ANY(:u)",
        "DELETE FROM lesson_slot_student WHERE student_id = ANY(:u)",
        "DELETE FROM lesson_slot WHERE teacher_id = ANY(:u)",
    ):
        await db.execute(text(sql), {"u": users})
    await db.commit()
    try:
        await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": users})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": users})
        await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
        await db.commit()
    except Exception:
        await db.rollback()


async def _quiet_student(db, prefix: str) -> tuple[int, int, int]:
    """Ученик с расписанием: последняя своя сдача и вход — 10 дней назад."""
    teacher = await _user(db, f"{prefix}-t", role="teacher")
    student = await _user(db, f"{prefix}-s")
    course = await _course(db)
    await _slot(db, teacher_id=teacher, student_id=student)
    await _submission(db, user_id=student, course_id=course, days_ago=10)
    await _presence(db, student_id=student, days_ago=10)
    return student, teacher, course


async def _is_quiet(db, student: int) -> bool:
    return student in await nudge.list_quiet(db, days=DAYS)


@pytest.mark.asyncio
async def test_quiet_student_gets_one_nudge(db):
    student, teacher, course = await _quiet_student(db, "tsk1177-main")
    try:
        assert await _is_quiet(db, student)
        res = await nudge.enqueue_nudges(db, days=DAYS)
        assert student in res["students"]
        row = (await db.execute(text(
            "SELECT title, content, payload FROM notifications "
            "WHERE user_id = :u AND kind = 'reengage_nudge'"
        ), {"u": student})).one()
        assert row[1] and row[2]["url"].endswith("/me/continue")
        # Повторный проход в тот же день второй строки не пишет.
        await nudge.enqueue_nudges(db, days=DAYS)
        cnt = (await db.execute(text(
            "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = 'reengage_nudge'"
        ), {"u": student})).scalar()
        assert cnt == 1
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(db):
    student, teacher, course = await _quiet_student(db, "tsk1177-dry")
    try:
        res = await nudge.enqueue_nudges(db, days=DAYS, dry_run=True)
        assert student in res["students"]
        cnt = (await db.execute(text(
            "SELECT count(*) FROM notifications WHERE user_id = :u"
        ), {"u": student})).scalar()
        assert cnt == 0
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_own_work_or_visit_clears_manual_mark_does_not(db):
    student, teacher, course = await _quiet_student(db, "tsk1177-work")
    try:
        await _submission(db, user_id=student, course_id=course, days_ago=1,
                          source="manual_teacher")
        assert await _is_quiet(db, student), "ручной зачёт — не работа ученика"
        await _presence(db, student_id=student, days_ago=2)
        assert not await _is_quiet(db, student), "заходил в кабинет — не затих"
        await _presence(db, student_id=student, days_ago=10)
        await _submission(db, user_id=student, course_id=course, days_ago=1)
        assert not await _is_quiet(db, student), "своя сдача снимает напоминание"
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["alumni", "test"])
async def test_alumni_and_test_plans_excluded(db, code):
    student, teacher, course = await _quiet_student(db, f"tsk1177-{code}")
    try:
        await _plan(db, student_id=student, code=code)
        assert not await _is_quiet(db, student)
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_break_and_no_schedule_excluded(db):
    student, teacher, course = await _quiet_student(db, "tsk1177-break")
    try:
        today = date.today()
        await db.execute(text(
            "INSERT INTO student_break (student_id, starts_on, ends_on) VALUES (:u, :a, :b)"
        ), {"u": student, "a": today - timedelta(days=3), "b": today + timedelta(days=3)})
        await db.commit()
        assert not await _is_quiet(db, student), "на перерыве не напоминаем"
        await db.execute(text("DELETE FROM student_break WHERE student_id = :u"), {"u": student})
        await db.execute(text(
            "UPDATE lesson_slot_student SET is_active = false WHERE student_id = :u"
        ), {"u": student})
        await db.commit()
        assert not await _is_quiet(db, student), "без расписания не напоминаем"
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_bot_endpoint_returns_only_nudges(client, db):
    student, teacher, course = await _quiet_student(db, "tsk1177-api")
    try:
        await nudge.enqueue_nudges(db, days=DAYS)
        r = await client.get(
            f"/api/v1/students/{student}/reengage-nudges/pending", headers=_service_headers()
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["kind"] == "reengage_nudge"
        assert body["items"][0]["content"]
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_cabinet_switch_gates_tick_without_restart():
    """Рубильник из кабинета читается в каждом проходе: выключен — ничего не пишем."""
    from app.core import settings_store

    settings_store.apply_local("reengage_nudge_enabled", False)
    try:
        assert await nudge.nudge_tick() == {"disabled": True}
    finally:
        settings_store.forget_local("reengage_nudge_enabled")


def test_settings_registered_for_cabinet():
    """Обе настройки видны администратору, порог с границами."""
    from app.core import settings_registry

    keys = {d.key: d for d in settings_registry.SETTINGS}
    assert keys["reengage_nudge_enabled"].kind == "bool"
    assert keys["reengage_nudge_enabled"].default is False
    days = keys["reengage_nudge_days"]
    assert days.default == 7 and days.min_value and days.max_value
