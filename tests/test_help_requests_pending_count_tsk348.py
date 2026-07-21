"""Integration HTTP-тесты tsk-348.

1) GET /api/v1/teacher/help-requests/pending-count — новый эндпоинт для
   TG_LMS bot-поллера и веб-бейджа учителя (раньше help_requests вообще
   не были видны ни одному push-механизму — только review-очередь).
2) inbox-уведомление ученику при reply / явном close / task-limit override
   (раньше эти действия учителя не давали ученику никакого push-сигнала —
   только пассивный `messages`-тред, который нужно было открыть самому).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_teacher(db):
    teacher = Users(
        email=f"tsk348-tch-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk348-tch", tg_id=None,
    )
    db.add(teacher)
    await db.flush()
    await identity_link_service.upsert_identity(db, teacher.id, "email", teacher.email)
    token, _, _ = await create_session(db, user_id=teacher.id)
    await db.commit()
    return teacher.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"tsk348-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk348-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_student_with_session(db) -> tuple[int, str]:
    student_id = await _create_student(db)
    token, _, _ = await create_session(db, user_id=student_id)
    await db.commit()
    return student_id, token


async def _pick_task(db) -> int:
    row = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет задач в БД")
    return int(row[0])


async def _seed_help_request(db, *, teacher_id: int, student_id: int, task_id: int,
                              request_type: str = "manual_help", status: str = "open") -> int:
    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id,
             assigned_teacher_id, created_at, updated_at, priority)
            VALUES (:status, :rt, false, '{}'::jsonb, :student_id, :task_id,
                    :teacher_id, now(), now(), 100)
            RETURNING id
        """),
        {"status": status, "rt": request_type, "student_id": student_id,
         "task_id": task_id, "teacher_id": teacher_id},
    )
    rid = r.scalar_one()
    await db.commit()
    return int(rid)


async def _link_student_teacher(db, *, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _cleanup(db, *, teacher_id: int, student_id: int, request_ids: list[int]):
    if request_ids:
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id = ANY(:r)"), {"r": request_ids})
        await db.execute(text("DELETE FROM help_requests WHERE id = ANY(:r)"), {"r": request_ids})
    await db.execute(text("DELETE FROM notifications WHERE user_id IN (:t, :s)"), {"t": teacher_id, "s": student_id})
    await db.execute(text("DELETE FROM messages WHERE sender_id IN (:t, :s) OR recipient_id IN (:t, :s)"), {"t": teacher_id, "s": student_id})
    await db.execute(text("DELETE FROM student_teacher_links WHERE student_id = :s AND teacher_id = :t"), {"s": student_id, "t": teacher_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id IN (:t, :s)"), {"t": teacher_id, "s": student_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id IN (:t, :s)"), {"t": teacher_id, "s": student_id})
    await db.commit()


@pytest.mark.asyncio
async def test_pending_count_requires_auth(client):
    resp = await client.get("/api/v1/teacher/help-requests/pending-count?teacher_id=1")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pending_count_wrong_teacher_id_403(db, client):
    teacher_id, token = await _setup_teacher(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/pending-count?teacher_id={teacher_id + 9999}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=teacher_id, request_ids=[])


@pytest.mark.asyncio
async def test_pending_count_counts_open_help_requests_both_types(db, client):
    """tsk-348: раньше этот эндпоинт вообще не существовал — заявки помощи
    были невидимы ни боту, ни вебу. count должен видеть и manual_help,
    и blocked_limit, и не видеть закрытые/чужие."""
    teacher_id, token = await _setup_teacher(db)
    student_id = await _create_student(db)
    task_id = await _pick_task(db)
    rid_manual = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id, request_type="manual_help")
    rid_blocked = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id, request_type="blocked_limit")
    rid_closed = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id, status="closed")
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/pending-count?teacher_id={teacher_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["oldest_created_at"] is not None
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, request_ids=[rid_manual, rid_blocked, rid_closed])


@pytest.mark.asyncio
async def test_reply_creates_inbox_notification_for_student(db, client):
    """tsk-348: раньше reply создавал только messages-запись (pull) —
    ученик не получал никакого push, пока сам не откроет переписку."""
    teacher_id, token = await _setup_teacher(db)
    student_id = await _create_student(db)
    task_id = await _pick_task(db)
    rid = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid}/reply",
            json={"teacher_id": teacher_id, "message": "Проверьте цикл — там опечатка."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        row = (
            await db.execute(
                text("SELECT kind, read_at FROM notifications WHERE user_id = :u AND kind = 'help_request_replied'"),
                {"u": student_id},
            )
        ).fetchone()
        assert row is not None, "reply_help_request должен создать inbox-уведомление ученику"
        assert row[1] is None  # непрочитанное

        payload_row = (
            await db.execute(
                text("SELECT payload FROM notifications WHERE user_id = :u AND kind = 'help_request_replied'"),
                {"u": student_id},
            )
        ).fetchone()
        assert payload_row[0]["task_id"] == task_id, (
            "payload должен содержать task_id — иначе SPW не сможет построить "
            "CTA «Перейти к заданию» (resolveNotificationCta ищет task_id)"
        )
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, request_ids=[rid])


@pytest.mark.asyncio
async def test_explicit_close_notifies_student_but_system_close_does_not(db, client):
    """tsk-348: явное закрытие учителем — пуш ученику; системный auto-close
    (closed_by=None, tsk-339) — без пуша, это не действие учителя."""
    teacher_id, token = await _setup_teacher(db)
    student_id = await _create_student(db)
    task_id = await _pick_task(db)
    rid_explicit = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    rid_system = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    try:
        resp = await client.post(
            f"/api/v1/teacher/help-requests/{rid_explicit}/close",
            json={"closed_by": teacher_id, "resolution_comment": "Решили вместе"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        from app.services.help_requests_service import close_help_request
        await close_help_request(db, rid_system, closed_by=None)
        await db.commit()

        rows = (
            await db.execute(
                text("SELECT payload->>'request_id' FROM notifications WHERE user_id = :u AND kind = 'help_request_closed'"),
                {"u": student_id},
            )
        ).fetchall()
        notified_ids = {int(r[0]) for r in rows}
        assert str(rid_explicit) in {r[0] for r in rows} or rid_explicit in notified_ids
        assert rid_system not in notified_ids

        payload_row = (
            await db.execute(
                text("SELECT payload FROM notifications WHERE user_id = :u AND kind = 'help_request_closed'"),
                {"u": student_id},
            )
        ).fetchone()
        assert payload_row[0]["task_id"] == task_id
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, request_ids=[rid_explicit, rid_system])


@pytest.mark.asyncio
async def test_new_help_request_notifies_assigned_teacher(db, client):
    """tsk-348 follow-up: бейдж без возможности прочитать не решает проблему —
    у учителя должна появиться настоящая inbox-запись (лента, как у ученика),
    не только число в счётчике. Создаём заявку через реальный API-путь
    request-help (тот самый, что вызывает живой инцидент), а не raw INSERT."""
    teacher_id, _teacher_token = await _setup_teacher(db)
    student_id, student_token = await _create_student_with_session(db)
    task_id = await _pick_task(db)
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    try:
        resp = await client.post(
            f"/api/v1/learning/tasks/{task_id}/request-help",
            json={"student_id": student_id, "message": "Не понимаю условие"},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        assert resp.status_code == 200
        request_id = resp.json()["request_id"]

        row = (
            await db.execute(
                text(
                    "SELECT payload, read_at FROM notifications "
                    "WHERE user_id = :u AND kind = 'help_request_opened'"
                ),
                {"u": teacher_id},
            )
        ).fetchone()
        assert row is not None, "новая заявка должна создать inbox-уведомление НАЗНАЧЕННОМУ учителю"
        assert row[1] is None
        assert row[0]["request_id"] == request_id
        assert row[0]["task_id"] == task_id
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_id=student_id, request_ids=[request_id])
