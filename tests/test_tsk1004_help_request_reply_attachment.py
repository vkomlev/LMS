"""tsk-1004: вложение к ОТВЕТУ преподавателя на заявку помощи (зеркало tsk-943).

У ЗАПРОСА (вопроса) ученика вложение уже было (tsk-943: `help_requests.attachment_*`).
У ОТВЕТА преподавателя — не было вовсе, хотя инфраструктура (namespace
`attachment_storage.HELP_REQUESTS`, поля `messages.attachment_url/attachment_id`)
уже существовала. Проверяем:

1. Преподаватель прикладывает файл к ответу тем же upload-эндпоинтом, что и
   ученик (`POST /learning/help-requests/attachments` — без ролевого гейта) —
   вложение попадает в `messages.attachment_id` сообщения-ответа.
2. Ученик видит вложение в своей ленте ответа (`replies[].attachment_url`) и
   может его скачать.
3. Преподаватель видит вложение своего ответа в карточке заявки (`history`) и
   тоже может его скачать.
4. ACL скачивания: чужой ученик — 403, посторонний преподаватель — 403,
   несуществующий message_id — 404.
5. Ответ без вложения — `attachment_url` остаётся null (регресс).
"""
from __future__ import annotations

import io
import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_TAG = "tsk1004"


async def _user(db, name: str) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен)."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG} {name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _course(db) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": f"{_TAG}-course-{random.randint(10**8, 10**10)}"},
    )
    return res.scalar_one()


async def _task(db, course_id: int) -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, is_active) "
            "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true) RETURNING id"
        ),
        {
            "e": f"{_TAG}-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"type": "SA", "stem": "x"}),
            "r": json.dumps({"max_score": 10}),
            "cid": course_id,
            "d": difficulty_id,
        },
    )
    return res.scalar_one()


async def _link_teacher(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id, linked_at) "
            "VALUES (:s, :t, now())"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _upload(
    client, token: str, *, filename: str = "reply-screenshot.png", content: bytes = b"teacher-png-bytes"
) -> dict:
    resp = await client.post(
        "/api/v1/learning/help-requests/attachments",
        headers=_bearer(token),
        files={"file": (filename, io.BytesIO(content), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _request_help(client, token: str, *, task_id: int, student_id: int) -> int:
    resp = await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "не понимаю"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["request_id"]


async def _cleanup(db, *, user_ids=(), task_ids=(), request_ids=()) -> None:
    for rid in request_ids:
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id=:r"), {"r": rid})
        await db.execute(text("DELETE FROM help_requests WHERE id=:r"), {"r": rid})
    for uid in user_ids:
        await db.execute(
            text("DELETE FROM messages WHERE sender_id=:u OR recipient_id=:u"), {"u": uid}
        )
        await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
    for tid in task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


async def test_reply_with_attachment_visible_to_student_and_teacher(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, s_token = await _user(db, "stud")
    teacher_id, t_token = await _user(db, "tch")
    await _link_teacher(db, student_id, teacher_id)
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    request_id = await _request_help(client, s_token, task_id=task_id, student_id=student_id)

    att = await _upload(client, t_token, filename="answer-screenshot.png", content=b"teacher-png-bytes")
    reply = await client.post(
        f"/api/v1/teacher/help-requests/{request_id}/reply",
        headers=_bearer(t_token),
        json={
            "teacher_id": teacher_id,
            "message": "Смотри мой скрин",
            "close_after_reply": False,
            "attachment_id": att["attachment_id"],
        },
    )
    assert reply.status_code == 200, reply.text
    message_id = reply.json()["message_id"]

    row = (
        await db.execute(text("SELECT attachment_id FROM messages WHERE id = :id"), {"id": message_id})
    ).fetchone()
    assert row.attachment_id == att["attachment_id"]

    # Ученик видит вложение в своей ленте ответа.
    state = await client.get(
        f"/api/v1/learning/tasks/{task_id}/help-request?student_id={student_id}",
        headers=_bearer(s_token),
    )
    assert state.status_code == 200, state.text
    replies = state.json()["replies"]
    assert len(replies) == 1
    assert replies[0]["message_id"] == message_id
    assert replies[0]["attachment_url"] == (
        f"/api/v1/learning/help-requests/{request_id}/replies/{message_id}/attachment"
    )

    # Ученик скачивает вложение ответа.
    download = await client.get(replies[0]["attachment_url"], headers=_bearer(s_token))
    assert download.status_code == 200, download.text
    assert download.content == b"teacher-png-bytes"

    # Преподаватель видит и скачивает своё вложение в карточке заявки.
    detail = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert detail.status_code == 200, detail.text
    history = detail.json()["history"]
    assert len(history) == 1
    assert history[0]["attachment_url"] == (
        f"/api/v1/teacher/help-requests/{request_id}/replies/{message_id}/attachment"
    )
    teacher_download = await client.get(
        history[0]["attachment_url"] + f"?teacher_id={teacher_id}", headers=_bearer(t_token)
    )
    assert teacher_download.status_code == 200, teacher_download.text
    assert teacher_download.content == b"teacher-png-bytes"

    await _cleanup(
        db, user_ids=[student_id, teacher_id], task_ids=[task_id], request_ids=[request_id]
    )


async def test_reply_without_attachment_leaves_url_null(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, s_token = await _user(db, "stud")
    teacher_id, t_token = await _user(db, "tch")
    await _link_teacher(db, student_id, teacher_id)
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    request_id = await _request_help(client, s_token, task_id=task_id, student_id=student_id)

    reply = await client.post(
        f"/api/v1/teacher/help-requests/{request_id}/reply",
        headers=_bearer(t_token),
        json={"teacher_id": teacher_id, "message": "Без вложения", "close_after_reply": False},
    )
    assert reply.status_code == 200, reply.text

    state = await client.get(
        f"/api/v1/learning/tasks/{task_id}/help-request?student_id={student_id}",
        headers=_bearer(s_token),
    )
    assert state.status_code == 200, state.text
    replies = state.json()["replies"]
    assert len(replies) == 1
    assert replies[0]["attachment_url"] is None

    await _cleanup(
        db, user_ids=[student_id, teacher_id], task_ids=[task_id], request_ids=[request_id]
    )


async def test_reply_attachment_acl(db, client, monkeypatch) -> None:
    from app.services import entitlements_service as ent
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")

    student_id, s_token = await _user(db, "stud")
    other_student_id, other_token = await _user(db, "other-stud")
    teacher_id, t_token = await _user(db, "tch")
    stranger_teacher_id, stranger_token = await _user(db, "stranger-tch")
    await _link_teacher(db, student_id, teacher_id)
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    request_id = await _request_help(client, s_token, task_id=task_id, student_id=student_id)
    att = await _upload(client, t_token)
    reply = await client.post(
        f"/api/v1/teacher/help-requests/{request_id}/reply",
        headers=_bearer(t_token),
        json={
            "teacher_id": teacher_id,
            "message": "Вот скрин",
            "attachment_id": att["attachment_id"],
        },
    )
    assert reply.status_code == 200, reply.text
    message_id = reply.json()["message_id"]
    url_student = f"/api/v1/learning/help-requests/{request_id}/replies/{message_id}/attachment"
    url_teacher = f"/api/v1/teacher/help-requests/{request_id}/replies/{message_id}/attachment"

    # Чужой ученик — 403.
    foreign = await client.get(url_student, headers=_bearer(other_token))
    assert foreign.status_code == 403

    # Посторонний преподаватель — 403.
    stranger = await client.get(
        f"{url_teacher}?teacher_id={stranger_teacher_id}", headers=_bearer(stranger_token)
    )
    assert stranger.status_code == 403

    # Несуществующий message_id — 404.
    missing = await client.get(
        f"/api/v1/learning/help-requests/{request_id}/replies/999999999/attachment",
        headers=_bearer(s_token),
    )
    assert missing.status_code == 404

    await _cleanup(
        db,
        user_ids=[student_id, other_student_id, teacher_id, stranger_teacher_id],
        task_ids=[task_id],
        request_ids=[request_id],
    )
