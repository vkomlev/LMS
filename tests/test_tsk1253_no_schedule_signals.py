"""tsk-1253: напоминания и сигнал «пропал» для учеников без расписания,
дубль на почту, привязка Telegram по глубокой ссылке.

По тесту на каждое решение оператора (2026-10-20):
- тариф без занятий (`self`) получает напоминание своим текстом, без расписания
  и без такого тарифа — нет;
- не больше двух напоминаний подряд, пока ученик не вернётся;
- сигнал «пропал» по активности: раньше был активен, 14 дней тишины, без
  расписания; «не начинал» и ученик с расписанием в эту ветку не попадают;
- письмо-дубль только при включённом рубильнике и не чаще раза в час; сообщение
  от сотрудника дублируется, от ученика — нет;
- ссылка на бота выдаётся, бот по ней привязывает Telegram один раз.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.core.config import Settings
from app.models.users import Users
from app.services import learning_gap_signals_service as gaps
from app.services import notify_email_mirror_service as mirror
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


async def _course(db) -> int:
    cid = int((await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES ('tsk1253', 'auto_check', false, :u) RETURNING id"
    ), {"u": f"tsk1253-{random.randint(10**8, 10**10)}"})).scalar_one())
    await db.commit()
    return cid


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(text(
        "INSERT INTO user_courses (user_id, course_id) VALUES (:u, :c)"
    ), {"u": user_id, "c": course_id})
    await db.commit()


async def _submission(db, *, user_id: int, course_id: int, days_ago: int) -> None:
    task_id = int((await db.execute(text(
        "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
        " course_id, difficulty_id, is_active) "
        "VALUES (:e, 1, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, 1, true) RETURNING id"
    ), {
        "e": f"tsk1253-t-{random.randint(10**8, 10**10)}",
        "c": json.dumps({"type": "SA", "stem": "2+2"}),
        "r": json.dumps({"answers": ["4"]}),
        "cid": course_id,
    })).scalar_one())
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await db.execute(text(
        "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
        " received_at, max_score, source_system, is_correct, answer_json) "
        "VALUES (1, :u, :t, :w, 0, :w, 1, 'spw_web', true, CAST(:a AS jsonb))"
    ), {"u": user_id, "t": task_id, "w": when,
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


async def _slot(db, *, teacher_id: int, student_id: int) -> None:
    gid = (await db.execute(text("SELECT min(id) FROM schedule_group"))).scalar()
    if gid is None:
        gid = (await db.execute(text(
            "INSERT INTO schedule_group (audience, subject, name) "
            "VALUES ('kids', 'tsk1253', 'tsk1253') RETURNING id"
        ))).scalar_one()
    slot_id = int((await db.execute(text(
        "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes, group_id) "
        "VALUES (:t, 1, '15:00', 60, :g) RETURNING id"
    ), {"t": teacher_id, "g": gid})).scalar_one())
    await db.execute(text(
        "INSERT INTO lesson_slot_student (slot_id, student_id) VALUES (:s, :u)"
    ), {"s": slot_id, "u": student_id})
    await db.commit()


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.execute(text(
        "DELETE FROM task_results WHERE task_id IN "
        "(SELECT id FROM tasks WHERE course_id = ANY(:c))"
    ), {"c": courses})
    await db.execute(text("DELETE FROM learning_gap_signal WHERE course_id = ANY(:c)"),
                     {"c": courses})
    await db.execute(text("DELETE FROM user_courses WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:c)"), {"c": courses})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:c)"), {"c": courses})
    for sql in (
        "DELETE FROM notifications WHERE user_id = ANY(:u)",
        "DELETE FROM student_presence WHERE student_id = ANY(:u)",
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


async def _self_student(db, prefix: str, *, quiet_days: int = 10) -> tuple[int, int]:
    """Ученик тарифа без занятий: последняя сдача и вход — `quiet_days` назад."""
    student = await _user(db, prefix)
    course = await _course(db)
    await _enroll(db, student, course)
    await _plan(db, student_id=student, code="self")
    await _submission(db, user_id=student, course_id=course, days_ago=quiet_days)
    await _presence(db, student_id=student, days_ago=quiet_days)
    return student, course


async def _age_nudges(db, student: int, days: int) -> None:
    """Сдвинуть напоминания в прошлое, как будто прошли `days` дней."""
    await db.execute(text(
        "UPDATE notifications SET modified_at = modified_at - make_interval(days => :d) "
        "WHERE user_id = :u AND kind = 'reengage_nudge'"
    ), {"u": student, "d": days})
    await db.commit()


# ------------------------------------------------------------ напоминание


@pytest.mark.asyncio
async def test_self_plan_without_schedule_gets_own_text(db):
    student, course = await _self_student(db, "tsk1253-self")
    try:
        res = await nudge.enqueue_nudges(db, days=DAYS)
        assert student in res["students"]
        content = (await db.execute(text(
            "SELECT content FROM notifications WHERE user_id = :u AND kind = 'reengage_nudge'"
        ), {"u": student})).scalar_one()
        assert content == nudge._BODY_NO_SCHEDULE
        assert "занятия" not in content
    finally:
        await _cleanup(db, [student], [course])


@pytest.mark.asyncio
async def test_no_schedule_and_no_self_plan_is_not_nudged(db):
    student, course = await _self_student(db, "tsk1253-noplan")
    try:
        await db.execute(text("DELETE FROM student_subscription WHERE student_id = :u"),
                         {"u": student})
        await db.commit()
        assert student not in await nudge.list_quiet(db, days=DAYS)
    finally:
        await _cleanup(db, [student], [course])


@pytest.mark.asyncio
async def test_scheduled_student_keeps_lesson_text(db):
    student, course = await _self_student(db, "tsk1253-sched")
    teacher = await _user(db, "tsk1253-sched-t", role="teacher")
    try:
        await db.execute(text("DELETE FROM student_subscription WHERE student_id = :u"),
                         {"u": student})
        await db.commit()
        await _slot(db, teacher_id=teacher, student_id=student)
        await nudge.enqueue_nudges(db, days=DAYS)
        content = (await db.execute(text(
            "SELECT content FROM notifications WHERE user_id = :u AND kind = 'reengage_nudge'"
        ), {"u": student})).scalar_one()
        assert content == nudge._BODY
    finally:
        await _cleanup(db, [student, teacher], [course])


@pytest.mark.asyncio
async def test_at_most_two_nudges_until_student_returns(db):
    student, course = await _self_student(db, "tsk1253-cap", quiet_days=40)
    try:
        await nudge.enqueue_nudges(db, days=DAYS)
        await _age_nudges(db, student, 8)
        await nudge.enqueue_nudges(db, days=DAYS)
        await _age_nudges(db, student, 8)
        assert student not in await nudge.list_quiet(db, days=DAYS), "третьего подряд нет"
        # Вернулся, потом снова затих — счёт заново.
        await _presence(db, student_id=student, days_ago=9)
        assert student in await nudge.list_quiet(db, days=DAYS)
    finally:
        await _cleanup(db, [student], [course])


# ------------------------------------------------------------ сигнал «пропал»


async def _activity_ids(db) -> set[int]:
    return {int(r["student_id"]) for r in await gaps.find_dropout_risk_by_activity(db, days=14)}


@pytest.mark.asyncio
async def test_activity_signal_for_student_without_schedule(db):
    student, course = await _self_student(db, "tsk1253-drop", quiet_days=20)
    try:
        assert student in await _activity_ids(db)
        await gaps.scan_and_create_signals(db)
        meta = (await db.execute(text(
            "SELECT meta FROM learning_gap_signal "
            "WHERE student_id = :u AND reason = 'dropout_risk'"
        ), {"u": student})).scalar_one()
        assert meta["mode"] == "activity" and meta["silence_days"] >= 19
    finally:
        await _cleanup(db, [student], [course])


@pytest.mark.asyncio
async def test_activity_signal_skips_recent_never_started_and_scheduled(db):
    recent, c1 = await _self_student(db, "tsk1253-recent", quiet_days=3)
    never = await _user(db, "tsk1253-never")
    c2 = await _course(db)
    await _enroll(db, never, c2)
    sched, c3 = await _self_student(db, "tsk1253-sched2", quiet_days=20)
    teacher = await _user(db, "tsk1253-sched2-t", role="teacher")
    try:
        await _slot(db, teacher_id=teacher, student_id=sched)
        ids = await _activity_ids(db)
        assert recent not in ids, "заходил 3 дня назад"
        assert never not in ids, "не начинал — это не «пропал»"
        assert sched not in ids, "с расписанием — основной датчик"
    finally:
        await _cleanup(db, [recent, never, sched, teacher], [c1, c2, c3])


# ------------------------------------------------------------ почта


@pytest.fixture
def sent(monkeypatch):
    """Перехват транспорта: список (кому, тема, html)."""
    out: list[tuple[str, str, str]] = []

    async def fake_send(settings, to, subject, html):
        out.append((to, subject, html))

    monkeypatch.setattr(mirror, "send_html_email", fake_send)
    monkeypatch.setattr(mirror, "email_configured", lambda s: True)
    return out


@pytest.mark.asyncio
async def test_email_mirror_off_by_default(db, sent):
    student = await _user(db, "tsk1253-mail-off")
    try:
        assert await mirror.mirror_to_email(
            db, user_id=student, kind=mirror.KIND_REENGAGE, url="https://x/me/continue",
        ) is False
        assert sent == []
    finally:
        await _cleanup(db, [student], [])


@pytest.mark.asyncio
async def test_email_mirror_once_per_hour_and_only_link(db, sent):
    student = await _user(db, "tsk1253-mail")
    teacher = await _user(db, "tsk1253-mail-t", role="teacher")
    other = await _user(db, "tsk1253-mail-s2")
    settings_store.apply_local(mirror.SETTING_KEY, True)
    try:
        assert await mirror.mirror_teacher_message(db, sender_id=other, recipient_id=student) is False
        assert await mirror.mirror_teacher_message(db, sender_id=teacher, recipient_id=student)
        assert len(sent) == 1 and sent[0][2].count("/me/messages") == 1
        assert await mirror.mirror_to_email(
            db, user_id=student, kind=mirror.KIND_REENGAGE, url="https://x/me/continue",
        ) is False, "второе письмо в течение часа не уходит"
        assert len(sent) == 1
    finally:
        settings_store.forget_local(mirror.SETTING_KEY)
        # audit_event — только дозапись (триггер), его строки не чистим.
        await _cleanup(db, [student, teacher, other], [])


def test_new_settings_registered_and_off():
    from app.core import settings_registry

    keys = {d.key: d for d in settings_registry.SETTINGS}
    assert keys["notify_email_mirror_enabled"].default is False
    assert keys["dropout_activity_days"].default == 14


# ------------------------------------------------------------ Telegram


@pytest.mark.asyncio
async def test_tg_deep_link_hidden_without_bot(client, db, monkeypatch):
    from app.services.auth.session_service import create_session

    monkeypatch.setenv("QUIZ_FUNNEL_BOT_USERNAME", "")
    student = await _user(db, "tsk1253-tg-nobot")
    token, _, _ = await create_session(db, user_id=student)
    await db.commit()
    try:
        r = await client.get("/api/v1/me/identity/tg/bot", cookies={"session": token})
        assert r.status_code == 200 and r.json() == {"available": False}
        r = await client.post("/api/v1/me/identity/tg/deep-link", cookies={"session": token})
        assert r.status_code == 404, r.text
    finally:
        await _cleanup(db, [student], [])


@pytest.mark.asyncio
async def test_tg_deep_link_binds_once(client, db, monkeypatch):
    from app.services.auth.session_service import create_session

    monkeypatch.setenv("QUIZ_FUNNEL_BOT_USERNAME", "@school_student_bot")
    student = await _user(db, "tsk1253-tg")
    token, _, _ = await create_session(db, user_id=student)
    await db.commit()
    tg_id = random.randint(10**9, 10**10)
    try:
        r = await client.post("/api/v1/me/identity/tg/deep-link", cookies={"session": token})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert url.startswith("https://t.me/school_student_bot?start=tg_")
        start_arg = url.split("start=", 1)[1]
        assert len(start_arg) <= 64, "предел Telegram на параметр /start"
        raw = start_arg[len("tg_"):]

        r = await client.post("/api/v1/integrations/student-bot/tg-link",
                              json={"token": raw, "tg_id": tg_id}, headers=_service_headers())
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == student
        got = (await db.execute(text("SELECT tg_id FROM users WHERE id = :u"),
                                {"u": student})).scalar()
        assert got == tg_id

        r = await client.post("/api/v1/integrations/student-bot/tg-link",
                              json={"token": raw, "tg_id": tg_id}, headers=_service_headers())
        assert r.status_code == 404, "ссылка одноразовая"
    finally:
        await _cleanup(db, [student], [])


@pytest.mark.asyncio
async def test_bot_link_requires_service_key(client):
    r = await client.post("/api/v1/integrations/student-bot/tg-link",
                          json={"token": "x" * 20, "tg_id": 1})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_teacher_message_mirrored_in_background(db, sent, monkeypatch):
    """send_message не ждёт письма: дубль уходит фоновой задачей своей сессией."""
    import asyncio

    from app.services.messages_service import MessagesService

    student = await _user(db, "tsk1253-bg")
    teacher = await _user(db, "tsk1253-bg-t", role="teacher")
    settings_store.apply_local(mirror.SETTING_KEY, True)

    class _SameSession:
        """Фоновая задача берёт сессию теста: строки теста не видны чужой сессии."""

        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    import app.db.session as session_module
    monkeypatch.setattr(session_module, "async_session_factory", lambda: _SameSession())
    try:
        await MessagesService().send_message(
            db, message_type="text", content="привет", recipient_id=student, sender_id=teacher,
        )
        await asyncio.gather(*list(mirror._background))
        assert len(sent) == 1 and "привет" not in sent[0][2]
    finally:
        settings_store.forget_local(mirror.SETTING_KEY)
        await db.execute(text("DELETE FROM messages WHERE recipient_id = :u"), {"u": student})
        await db.commit()
        await _cleanup(db, [student, teacher], [])
