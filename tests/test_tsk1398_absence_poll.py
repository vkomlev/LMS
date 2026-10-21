"""tsk-1398, этапы 3–5: опрос о пропуске, ответы из мессенджеров, срочная связь.

- не вошёл через 5 минут → вопрос с кнопками в подключённый канал, один раз;
- без каналов — не спрашиваем; рубильник выключен — не спрашиваем;
- «Опаздываю» откладывает автоотметку неявки до конца занятия;
- причина ложится в отметку о пропуске без преподавателя; отметку преподавателя не перетирает;
- нет ответа до конца занятия → `no_answer`;
- обычное сообщение из мессенджера → в переписку, в ветку последнего сотрудника;
- срочное сообщение: только сотрудникам, полный текст, история, 409 без каналов.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import settings_store
from app.models.users import Users
from app.services import absence_poll_service as polls
from app.services import lesson_attendance_cron_service as cron
from app.services import messenger_channel_service as svc
from app.services.auth import identity_link_service

MAX_SECRET = "max-secret-test"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.setenv("MAX_BOT_USERNAME", "school_bot")
    monkeypatch.setenv("MAX_WEBHOOK_SECRET", MAX_SECRET)
    monkeypatch.setenv("STUDENT_BOT_TOKEN", "")
    sent: list[dict] = []

    async def fake_send(channel, external_id, message, settings=None, buttons=None):
        sent.append({"channel": channel, "to": external_id, "text": message, "buttons": buttons})

    monkeypatch.setattr(svc, "send_text", fake_send)
    settings_store.apply_local(polls.SETTING_ENABLED, True)
    settings_store.apply_local(polls.SETTING_DELAY, 5)
    yield sent
    settings_store.forget_local(polls.SETTING_ENABLED)
    settings_store.forget_local(polls.SETTING_DELAY)


async def _user(db, role: str) -> int:
    email = f"tsk1398p-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"tsk1398 {role}", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE name = :r"
    ), {"u": u.id, "r": role})
    await db.commit()
    return u.id


async def _lesson(db, teacher: int, student: int, *, started_min_ago: int, duration: int = 60) -> tuple[int, int]:
    at = datetime.now(timezone.utc) - timedelta(minutes=started_min_ago)
    occ = int((await db.execute(text(
        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
        "VALUES (:t, :at, :d) RETURNING id"
    ), {"t": teacher, "at": at, "d": duration})).scalar_one())
    part = int((await db.execute(text(
        "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
        "VALUES (:o, :s, 'scheduled') RETURNING id"
    ), {"o": occ, "s": student})).scalar_one())
    await db.commit()
    return occ, part


async def _link_max(db, student: int) -> str:
    ext = str(random.randint(10**8, 10**9))
    await db.execute(text(
        "INSERT INTO messenger_link (user_id, channel, external_id) VALUES (:u, 'max', :e)"
    ), {"u": student, "e": ext})
    await db.commit()
    return ext


async def _cleanup(db, users: list[int], occs: list[int]) -> None:
    await db.rollback()
    await db.execute(text("DELETE FROM lesson_occurrence WHERE id = ANY(:o)"), {"o": occs})
    for sql in (
        "DELETE FROM messages WHERE sender_id = ANY(:u) OR recipient_id = ANY(:u)",
        "DELETE FROM notifications WHERE user_id = ANY(:u)",
        "DELETE FROM messenger_link WHERE user_id = ANY(:u)",
    ):
        await db.execute(text(sql), {"u": users})
    await db.commit()


async def _followup(db, student: int, occ: int):
    return (await db.execute(text(
        "SELECT reason, asked_by, note FROM lesson_absence_followup WHERE student_id = :s AND occurrence_id = :o"
    ), {"s": student, "o": occ})).first()


async def _status(db, part: int) -> str:
    return (await db.execute(text(
        "SELECT status FROM lesson_occurrence_participant WHERE id = :p"), {"p": part})).scalar()


@pytest.mark.asyncio
async def test_asks_once_with_buttons_only_linked(db, env):
    teacher, student, lonely = await _user(db, "teacher"), await _user(db, "student"), await _user(db, "student")
    occ, _ = await _lesson(db, teacher, student, started_min_ago=6)
    occ2, _ = await _lesson(db, teacher, lonely, started_min_ago=6)
    ext = await _link_max(db, student)
    try:
        assert await polls.ask_due(db) == 1
        await db.commit()
        assert len(env) == 1 and env[0]["to"] == ext
        assert env[0]["buttons"] == list(polls.ANSWERS.values())
        assert await polls.ask_due(db) == 0, "второй раз не спрашиваем"
    finally:
        await _cleanup(db, [teacher, student, lonely], [occ, occ2])


@pytest.mark.asyncio
async def test_not_asked_before_delay_or_when_off(db, env):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, _ = await _lesson(db, teacher, student, started_min_ago=2)
    await _link_max(db, student)
    try:
        assert await polls.ask_due(db) == 0
        settings_store.apply_local(polls.SETTING_ENABLED, False)
        await db.execute(text("UPDATE lesson_occurrence SET scheduled_at = now() - interval '7 min' WHERE id = :o"),
                         {"o": occ})
        await db.commit()
        assert await polls.ask_due(db) == 0
    finally:
        await _cleanup(db, [teacher, student], [occ])


@pytest.mark.asyncio
async def test_reason_answer_becomes_followup_after_no_show(client, db, env, db_session_factory):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, part = await _lesson(db, teacher, student, started_min_ago=6)
    ext = await _link_max(db, student)
    headers = {"X-Max-Bot-Api-Secret": MAX_SECRET}
    try:
        await polls.ask_due(db)
        await db.commit()
        r = await client.post("/api/v1/integrations/max/webhook", headers=headers, json={
            "update_type": "message_created",
            "message": {"sender": {"user_id": int(ext)}, "body": {"text": polls.ANSWERS["illness"]}},
        })
        assert r.status_code == 200
        assert env[-1]["text"] == polls.REPLIES["reason"]
        # Автоотметка неявки + перенос причины.
        await db.execute(text("UPDATE lesson_occurrence SET scheduled_at = now() - interval '15 min' WHERE id = :o"),
                         {"o": occ})
        await db.commit()
        await cron.lesson_attendance_cron_tick(db_session_factory)
        assert await _status(db, part) == "no_show"
        reason, asked_by, note = await _followup(db, student, occ)
        assert reason == "illness" and asked_by is None and "MAX" in note
        # Ответ не попал в переписку как обычное сообщение.
        n = (await db.execute(text("SELECT count(*) FROM messages WHERE sender_id = :s"), {"s": student})).scalar()
        assert n == 0
    finally:
        await _cleanup(db, [teacher, student], [occ])


@pytest.mark.asyncio
async def test_late_postpones_no_show_and_tells_teacher(db, env, db_session_factory):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, part = await _lesson(db, teacher, student, started_min_ago=6)
    await _link_max(db, student)
    try:
        await polls.ask_due(db)
        await db.commit()
        assert await polls.handle_answer(db, student_id=student, channel="max",
                                         body=polls.ANSWERS["late"]) == polls.REPLIES["late"]
        await db.execute(text("UPDATE lesson_occurrence SET scheduled_at = now() - interval '20 min' WHERE id = :o"),
                         {"o": occ})
        await db.commit()
        await cron.lesson_attendance_cron_tick(db_session_factory)
        assert await _status(db, part) == "scheduled", "опаздывающему неявку не ставим до конца занятия"
        kinds = (await db.execute(text("SELECT kind FROM notifications WHERE user_id = :t"),
                                  {"t": teacher})).scalars().all()
        assert "student_running_late" in kinds
    finally:
        await _cleanup(db, [teacher, student], [occ])


@pytest.mark.asyncio
async def test_no_answer_after_end_and_teacher_mark_wins(db, env, db_session_factory):
    teacher, student, other = await _user(db, "teacher"), await _user(db, "student"), await _user(db, "student")
    occ, part = await _lesson(db, teacher, student, started_min_ago=6, duration=30)
    occ2, part2 = await _lesson(db, teacher, other, started_min_ago=6, duration=30)
    await _link_max(db, student)
    await _link_max(db, other)
    try:
        await polls.ask_due(db)
        await db.commit()
        # Преподаватель сам отметил причину у второго ученика.
        await db.execute(text(
            "INSERT INTO lesson_absence_followup (student_id, occurrence_id, asked_by, reason) "
            "VALUES (:s, :o, :t, 'makeup')"), {"s": other, "o": occ2, "t": teacher})
        await db.execute(text(
            "UPDATE lesson_occurrence SET scheduled_at = now() - interval '40 min' WHERE id = ANY(:o)"
        ), {"o": [occ, occ2]})
        await db.commit()
        await cron.lesson_attendance_cron_tick(db_session_factory)
        assert (await _followup(db, student, occ))[0] == "no_answer"
        assert (await _followup(db, other, occ2))[0] == "makeup"
    finally:
        await _cleanup(db, [teacher, student, other], [occ, occ2])


@pytest.mark.asyncio
async def test_plain_reply_goes_to_last_staff_thread(client, db, env):
    from app.services.messages_service import MessagesService

    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    ext = await _link_max(db, student)
    try:
        root = await MessagesService().send_message(
            db, message_type="text", content={"text": "Как дела с задачей?"},
            recipient_id=student, sender_id=teacher,
        )
        r = await client.post("/api/v1/integrations/max/webhook",
                              headers={"X-Max-Bot-Api-Secret": MAX_SECRET}, json={
            "update_type": "message_created",
            "message": {"sender": {"user_id": int(ext)}, "body": {"text": "Решил, спасибо!"}},
        })
        assert r.status_code == 200
        row = (await db.execute(text(
            "SELECT recipient_id, thread_id, source_system, content->>'text' FROM messages "
            "WHERE sender_id = :s"), {"s": student})).first()
        assert row == (teacher, root.thread_id or root.id, "max", "Решил, спасибо!")
    finally:
        await _cleanup(db, [teacher, student], [])


@pytest.mark.asyncio
async def test_urgent_message(client, db, env):
    from app.services.auth.session_service import create_session

    teacher, student, lonely = await _user(db, "teacher"), await _user(db, "student"), await _user(db, "student")
    ext = await _link_max(db, student)
    t_token, _, _ = await create_session(db, user_id=teacher)
    s_token, _, _ = await create_session(db, user_id=student)
    await db.commit()
    try:
        url = f"/api/v1/teacher/students/{student}/urgent-message"
        r = await client.post(url, json={"text": "Занятие переносится на 18:00"}, cookies={"session": s_token})
        assert r.status_code == 403, "ученику нельзя"
        r = await client.post(url, json={"text": "Занятие переносится на 18:00"}, cookies={"session": t_token})
        assert r.status_code == 200, r.text
        assert r.json()["delivered"] == ["max"]
        assert env[-1]["to"] == ext and "Занятие переносится на 18:00" in env[-1]["text"]
        src = (await db.execute(text("SELECT source_system FROM messages WHERE id = :m"),
                                {"m": r.json()["message_id"]})).scalar()
        assert src == "urgent"
        r = await client.post(f"/api/v1/teacher/students/{lonely}/urgent-message",
                              json={"text": "x"}, cookies={"session": t_token})
        assert r.status_code == 409
    finally:
        await _cleanup(db, [teacher, student, lonely], [])


def test_parse_answer():
    assert polls.parse_answer("Не приду: болею") == "illness"
    assert polls.parse_answer("  опаздываю, скоро буду ") == "late"
    assert polls.parse_answer("привет") is None


@pytest.mark.asyncio
async def test_undelivered_question_is_retried(db, env, monkeypatch):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, part = await _lesson(db, teacher, student, started_min_ago=6)
    await _link_max(db, student)

    async def broken(*a, **k):
        raise RuntimeError("сеть")

    try:
        monkeypatch.setattr(svc, "send_text", broken)
        assert await polls.ask_due(db) == 0
        n = (await db.execute(text("SELECT count(*) FROM absence_poll WHERE participant_id = :p"), {"p": part})).scalar()
        assert n == 0, "недоставленный вопрос не должен блокировать повтор"
    finally:
        await _cleanup(db, [teacher, student], [occ])


@pytest.mark.asyncio
async def test_repeated_late_answer_notifies_once(db, env):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, _ = await _lesson(db, teacher, student, started_min_ago=6)
    await _link_max(db, student)
    try:
        await polls.ask_due(db)
        await db.commit()
        for _ in range(2):
            await polls.handle_answer(db, student_id=student, channel="max", body=polls.ANSWERS["late"])
        n = (await db.execute(text(
            "SELECT count(*) FROM notifications WHERE user_id = :t AND kind = 'student_running_late'"
        ), {"t": teacher})).scalar()
        assert n == 1
    finally:
        await _cleanup(db, [teacher, student], [occ])


@pytest.mark.asyncio
async def test_answer_kept_when_no_show_threshold_longer_than_lesson(db, env):
    teacher, student = await _user(db, "teacher"), await _user(db, "student")
    occ, part = await _lesson(db, teacher, student, started_min_ago=6, duration=20)
    await _link_max(db, student)
    try:
        await polls.ask_due(db)
        await db.commit()
        await polls.handle_answer(db, student_id=student, channel="max", body=polls.ANSWERS["busy"])
        # Занятие кончилось, а неявку ещё не отметили — ответ не теряется.
        await db.execute(text("UPDATE lesson_occurrence SET scheduled_at = now() - interval '30 min' WHERE id = :o"),
                         {"o": occ})
        await db.commit()
        assert await polls.apply_due(db) == 0
        await db.execute(text("UPDATE lesson_occurrence_participant SET status = 'no_show' WHERE id = :p"), {"p": part})
        assert await polls.apply_due(db) == 1
        assert (await _followup(db, student, occ))[0] == "busy"
    finally:
        await _cleanup(db, [teacher, student], [occ])

