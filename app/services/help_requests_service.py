"""
Сервис заявок на помощь (Learning Engine V1, этап 3.8 / 3.8.1).

- Создание/обновление заявки при request-help (manual_help).
- Auto-create при BLOCKED_LIMIT (blocked_limit, этап 3.8.1).
- Назначение преподавателя (student_teacher_links → teacher_courses).
- ACL: teacher/methodist по назначению, связям или роли.
- Закрытие и ответ с идемпотентностью.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.help_requests import HelpRequests
from app.models.help_request_replies import HelpRequestReplies
from app.services.learning_events_service import (
    record_help_request_opened,
    record_help_request_closed,
    record_help_request_replied,
    record_attempt_limit_reached,
)
from app.services.messages_service import MessagesService
from app.services.student_teacher_links_service import StudentTeacherLinksService
from app.services.teacher_courses_service import TeacherCoursesService

logger = logging.getLogger(__name__)


def _task_title_display(task_id: int, external_uid: Optional[str]) -> str:
    """Заголовок задания для отображения (MVP)."""
    if external_uid:
        return external_uid
    return f"Задание #{task_id}"


async def resolve_assigned_teacher(
    db: AsyncSession,
    student_id: int,
    course_id: Optional[int],
) -> Optional[int]:
    """
    MVP: первый доступный преподаватель из student_teacher_links;
    fallback — из teacher_courses по course_id.
    """
    links_svc = StudentTeacherLinksService()
    teachers = await links_svc.list_teachers(db, student_id)
    if teachers:
        return teachers[0].id
    if course_id is not None:
        tc_svc = TeacherCoursesService()
        teachers_list, _ = await tc_svc.list_teachers(db, course_id, limit=1)
        if teachers_list:
            return teachers_list[0].id
    return None


async def get_or_create_help_request(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    event_id: int,
    message: Optional[str] = None,
    course_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    deduplicated: bool = False,
) -> Tuple[int, bool]:
    """
    После record_help_requested: получить или создать запись в help_requests.
    Если deduplicated и заявка с event_id уже есть — вернуть её id и created=False.
    Иначе создать новую, записать help_request_opened, вернуть (id, True).
    """
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE event_id = :event_id LIMIT 1"),
        {"event_id": event_id},
    )
    row = r.fetchone()
    if row is not None:
        await db.execute(
            text("UPDATE help_requests SET updated_at = now() WHERE id = :id"),
            {"id": row[0]},
        )
        return (int(row[0]), False)

    assigned = await resolve_assigned_teacher(db, student_id, course_id)
    msg_truncated = (message or "")[:2000] if message else None

    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id, course_id, attempt_id, event_id, message, assigned_teacher_id, created_at, updated_at)
            VALUES ('open', 'manual_help', false, '{}'::jsonb, :student_id, :task_id, :course_id, :attempt_id, :event_id, :message, :assigned_teacher_id, now(), now())
            RETURNING id
        """),
        {
            "student_id": student_id,
            "task_id": task_id,
            "course_id": course_id,
            "attempt_id": attempt_id,
            "event_id": event_id,
            "message": msg_truncated,
            "assigned_teacher_id": assigned,
        },
    )
    new_id = r.scalar()
    await record_help_request_opened(
        db, student_id, new_id, event_id, task_id, course_id
    )
    return (int(new_id), True)


async def get_or_create_blocked_limit_help_request(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    course_id: Optional[int] = None,
    attempt_id: Optional[int] = None,
    attempts_used: int = 0,
    attempts_limit_effective: int = 3,
    last_based_status: str = "BLOCKED_LIMIT",
) -> Tuple[int, bool, bool]:
    """
    Получить или создать open заявку типа blocked_limit для пары (student_id, task_id).
    Идемпотентно: одна open заявка blocked_limit на пару; повтор — обновление updated_at/context.
    Returns: (request_id, created, deduplicated).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": task_id},
    )
    r = await db.execute(
        text("""
            SELECT id, attempt_id, context_json FROM help_requests
            WHERE student_id = :student_id AND task_id = :task_id
              AND status = 'open' AND request_type = 'blocked_limit'
            LIMIT 1
        """),
        {"student_id": student_id, "task_id": task_id},
    )
    row = r.fetchone()
    context = {
        "attempts_used": attempts_used,
        "attempts_limit_effective": attempts_limit_effective,
        "last_based_status": last_based_status,
        "trigger": "blocked_limit",
    }
    context_str = json.dumps(context)
    if row is not None:
        request_id = int(row[0])
        await db.execute(
            text("""
                UPDATE help_requests
                SET updated_at = now(), attempt_id = COALESCE(:attempt_id, attempt_id),
                    context_json = :context_json
                WHERE id = :id
            """),
            {"id": request_id, "attempt_id": attempt_id, "context_json": context_str},
        )
        await record_attempt_limit_reached(
            db, student_id, request_id, task_id, attempts_used, attempts_limit_effective, course_id
        )
        return (request_id, False, True)
    assigned = await resolve_assigned_teacher(db, student_id, course_id)
    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id, course_id, attempt_id, event_id, assigned_teacher_id, created_at, updated_at)
            VALUES ('open', 'blocked_limit', true, :context_json, :student_id, :task_id, :course_id, :attempt_id, NULL, :assigned_teacher_id, now(), now())
            RETURNING id
        """),
        {
            "context_json": context_str,
            "student_id": student_id,
            "task_id": task_id,
            "course_id": course_id,
            "attempt_id": attempt_id,
            "assigned_teacher_id": assigned,
        },
    )
    new_id = r.scalar()
    await record_attempt_limit_reached(
        db, student_id, new_id, task_id, attempts_used, attempts_limit_effective, course_id
    )
    return (int(new_id), True, False)


async def help_request_exists(db: AsyncSession, request_id: int) -> bool:
    """Проверка существования заявки по id (без ACL)."""
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE id = :request_id LIMIT 1"),
        {"request_id": request_id},
    )
    return r.fetchone() is not None


async def can_access_help_request(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
) -> bool:
    """
    Доступ: assigned_teacher_id = teacher_id, или связь student_teacher_links,
    или teacher_courses по course_id, или роль methodist.
    """
    r = await db.execute(
        text("""
            SELECT hr.id, hr.assigned_teacher_id, hr.student_id, hr.course_id
            FROM help_requests hr
            WHERE hr.id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return False
    rid, assigned, student_id, course_id = row[0], row[1], row[2], row[3]

    if assigned == teacher_id:
        return True
    r = await db.execute(
        text("""
            SELECT 1 FROM student_teacher_links
            WHERE student_id = :student_id AND teacher_id = :teacher_id LIMIT 1
        """),
        {"student_id": student_id, "teacher_id": teacher_id},
    )
    if r.fetchone() is not None:
        return True
    if course_id is not None:
        r = await db.execute(
            text("""
                SELECT 1 FROM teacher_courses
                WHERE teacher_id = :teacher_id AND course_id = :course_id LIMIT 1
            """),
            {"teacher_id": teacher_id, "course_id": course_id},
        )
        if r.fetchone() is not None:
            return True
    r = await db.execute(
        text("""
            SELECT 1 FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = :teacher_id AND r.name = 'methodist' LIMIT 1
        """),
        {"teacher_id": teacher_id},
    )
    if r.fetchone() is not None:
        return True
    return False


async def list_help_requests(
    db: AsyncSession,
    teacher_id: int,
    status_filter: str = "open",
    request_type_filter: str = "all",
    limit: int = 20,
    offset: int = 0,
) -> Tuple[list[dict[str, Any]], int]:
    """
    Список заявок с ACL. status_filter: open | closed | all.
    request_type_filter: manual_help | blocked_limit | all (этап 3.8.1).
    Возвращает (items, total). items — словари для HelpRequestListItem.
    """
    status_cond = ""
    if status_filter == "open":
        status_cond = "AND hr.status = 'open'"
    elif status_filter == "closed":
        status_cond = "AND hr.status = 'closed'"
    type_cond = ""
    if request_type_filter == "manual_help":
        type_cond = "AND hr.request_type = 'manual_help'"
    elif request_type_filter == "blocked_limit":
        type_cond = "AND hr.request_type = 'blocked_limit'"

    acl_sql = """
        (hr.assigned_teacher_id = :teacher_id
         OR EXISTS (SELECT 1 FROM student_teacher_links stl WHERE stl.student_id = hr.student_id AND stl.teacher_id = :teacher_id)
         OR (hr.course_id IS NOT NULL AND EXISTS (SELECT 1 FROM teacher_courses tc WHERE tc.course_id = hr.course_id AND tc.teacher_id = :teacher_id))
         OR EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = :teacher_id AND r.name = 'methodist'))
    """
    params: dict[str, Any] = {"teacher_id": teacher_id}

    r = await db.execute(
        text(f"""
            SELECT COUNT(*) FROM help_requests hr
            WHERE {acl_sql} {status_cond} {type_cond}
        """),
        params,
    )
    total = r.scalar() or 0

    r = await db.execute(
        text(f"""
            SELECT hr.id, hr.status, hr.request_type, hr.auto_created, hr.context_json,
                   hr.student_id, hr.task_id, hr.course_id, hr.attempt_id,
                   hr.created_at, hr.updated_at, hr.thread_id, hr.event_id,
                   u.full_name AS student_name,
                   t.external_uid AS task_external_uid,
                   c.title AS course_title
            FROM help_requests hr
            LEFT JOIN users u ON u.id = hr.student_id
            LEFT JOIN tasks t ON t.id = hr.task_id
            LEFT JOIN courses c ON c.id = hr.course_id
            WHERE {acl_sql} {status_cond} {type_cond}
            ORDER BY hr.updated_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    )
    rows = r.fetchall()
    items = []
    for row in rows:
        ctx = row[4] if row[4] is not None else {}
        items.append({
            "request_id": row[0],
            "status": row[1],
            "request_type": row[2] or "manual_help",
            "auto_created": bool(row[3]) if row[3] is not None else False,
            "context": ctx if isinstance(ctx, dict) else {},
            "student_id": row[5],
            "task_id": row[6],
            "course_id": row[7],
            "attempt_id": row[8],
            "created_at": row[9],
            "updated_at": row[10],
            "thread_id": row[11],
            "event_id": row[12],
            "student_name": row[13],
            "task_title": _task_title_display(row[6], row[14]) if row[14] or row[6] else None,
            "course_title": row[15],
        })
    return (items, total)


async def get_help_request_detail(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Карточка заявки с историей ответов.
    Возвращает (detail_dict, error). error: None | "not_found" | "forbidden".
    """
    r = await db.execute(
        text("SELECT id FROM help_requests WHERE id = :request_id"),
        {"request_id": request_id},
    )
    if r.fetchone() is None:
        return (None, "not_found")
    ok = await can_access_help_request(db, request_id, teacher_id)
    if not ok:
        return (None, "forbidden")

    r = await db.execute(
        text("""
            SELECT hr.id, hr.status, hr.student_id, hr.task_id, hr.course_id, hr.attempt_id,
                   hr.created_at, hr.updated_at, hr.thread_id, hr.event_id,
                   hr.request_type, hr.auto_created, hr.context_json,
                   hr.message, hr.closed_at, hr.closed_by, hr.resolution_comment,
                   u.full_name AS student_name,
                   t.external_uid AS task_external_uid,
                   c.title AS course_title
            FROM help_requests hr
            LEFT JOIN users u ON u.id = hr.student_id
            LEFT JOIN tasks t ON t.id = hr.task_id
            LEFT JOIN courses c ON c.id = hr.course_id
            WHERE hr.id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, "not_found")
    ctx = row[12] if row[12] is not None else {}
    if not isinstance(ctx, dict):
        ctx = {}

    r2 = await db.execute(
        text("""
            SELECT id, teacher_id, message_id, body, close_after_reply, created_at
            FROM help_request_replies
            WHERE request_id = :request_id
            ORDER BY created_at ASC
        """),
        {"request_id": request_id},
    )
    replies = [
        {
            "reply_id": r[0],
            "teacher_id": r[1],
            "message_id": r[2],
            "body": r[3],
            "close_after_reply": r[4],
            "created_at": r[5],
        }
        for r in r2.fetchall()
    ]

    return ({
        "request_id": row[0],
        "status": row[1],
        "student_id": row[2],
        "task_id": row[3],
        "course_id": row[4],
        "attempt_id": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "thread_id": row[8],
        "event_id": row[9],
        "request_type": row[10] or "manual_help",
        "auto_created": bool(row[11]) if row[11] is not None else False,
        "context": ctx,
        "message": row[13],
        "closed_at": row[14],
        "closed_by": row[15],
        "resolution_comment": row[16],
        "student_name": row[17],
        "task_title": _task_title_display(row[3], row[18]) if row[18] or row[3] else None,
        "course_title": row[19],
        "history": replies,
    }, None)


async def close_help_request(
    db: AsyncSession,
    request_id: int,
    closed_by: int,
    resolution_comment: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[bool]]:
    """
    Закрыть заявку. Возвращает (data_dict, already_closed).
    data_dict: request_id, status, closed_at, updated_at. Если заявка не найдена — (None, None).
    """
    r = await db.execute(
        text("""
            SELECT id, status, student_id FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, None)
    hid, current_status, student_id = row[0], row[1], row[2]

    if current_status == "closed":
        r = await db.execute(
            text("SELECT closed_at, updated_at FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
        rw = r.fetchone()
        return ({
            "request_id": request_id,
            "status": "closed",
            "closed_at": rw[0],
            "updated_at": rw[1],
            "already_closed": True,
        }, True)

    now = datetime.now(timezone.utc)
    comment_truncated = (resolution_comment or "")[:2000] or None
    await db.execute(
        text("""
            UPDATE help_requests
            SET status = 'closed', closed_at = :closed_at, closed_by = :closed_by,
                resolution_comment = :resolution_comment, updated_at = :updated_at
            WHERE id = :id
        """),
        {
            "id": request_id,
            "closed_at": now,
            "closed_by": closed_by,
            "resolution_comment": comment_truncated,
            "updated_at": now,
        },
    )
    await record_help_request_closed(db, student_id, request_id, closed_by, resolution_comment)
    return ({
        "request_id": request_id,
        "status": "closed",
        "closed_at": now,
        "updated_at": now,
        "already_closed": False,
    }, False)


async def reply_help_request(
    db: AsyncSession,
    request_id: int,
    teacher_id: int,
    message: str,
    close_after_reply: bool = False,
    idempotency_key: Optional[str] = None,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Ответ на заявку: отправить сообщение студенту, записать reply, опционально закрыть.
    Возвращает (response_dict, error). error: None | "not_found" | "forbidden" | "closed".
    response_dict: request_id, message_id, thread_id, request_status, deduplicated.
    """
    r = await db.execute(
        text("""
            SELECT id, student_id, status, thread_id FROM help_requests WHERE id = :request_id
        """),
        {"request_id": request_id},
    )
    row = r.fetchone()
    if row is None:
        return (None, "not_found")
    hid, student_id, req_status, thread_id = row[0], row[1], row[2], row[3]

    if req_status == "closed":
        return (None, "closed")

    ok = await can_access_help_request(db, request_id, teacher_id)
    if not ok:
        return (None, "forbidden")

    if idempotency_key:
        r = await db.execute(
            text("""
                SELECT message_id, thread_id FROM help_request_replies
                WHERE request_id = :request_id AND idempotency_key = :key LIMIT 1
            """),
            {"request_id": request_id, "key": idempotency_key},
        )
        dup = r.fetchone()
        if dup is not None:
            return ({
                "request_id": request_id,
                "message_id": dup[0],
                "thread_id": dup[1],
                "request_status": req_status,
                "deduplicated": True,
            }, None)

    messages_svc = MessagesService()
    body_trimmed = message[:4000] if len(message) > 4000 else message
    content: dict[str, str] = {"text": body_trimmed}

    msg = await messages_svc.send_message(
        db,
        message_type="teacher_reply",
        content=content,
        recipient_id=student_id,
        sender_id=teacher_id,
        source_system="help_request_reply",
        thread_id=thread_id,
    )
    await db.flush()
    new_thread_id = msg.thread_id or msg.id
    if thread_id is None:
        await db.execute(
            text("UPDATE help_requests SET thread_id = :tid, updated_at = now() WHERE id = :id"),
            {"tid": new_thread_id, "id": request_id},
        )
        await db.flush()
    thread_id = thread_id or new_thread_id

    key_val = idempotency_key[:128] if idempotency_key else None
    await db.execute(
        text("""
            INSERT INTO help_request_replies (request_id, teacher_id, message_id, body, close_after_reply, idempotency_key, created_at)
            VALUES (:request_id, :teacher_id, :message_id, :body, :close_after_reply, :idem_key, now())
        """),
        {
            "request_id": request_id,
            "teacher_id": teacher_id,
            "message_id": msg.id,
            "body": body_trimmed,
            "close_after_reply": close_after_reply,
            "idem_key": key_val,
        },
    )
    await record_help_request_replied(db, student_id, request_id, teacher_id, msg.id, thread_id)

    final_status = req_status
    if close_after_reply:
        await db.execute(
            text("""
                UPDATE help_requests
                SET status = 'closed', closed_at = now(), closed_by = :closed_by, updated_at = now()
                WHERE id = :id
            """),
            {"id": request_id, "closed_by": teacher_id},
        )
        await record_help_request_closed(db, student_id, request_id, teacher_id, None)
        final_status = "closed"

    return ({
        "request_id": request_id,
        "message_id": msg.id,
        "thread_id": thread_id,
        "request_status": final_status,
        "deduplicated": False,
    }, None)
