"""tsk-431 (Календарь LMS Фаза 4): GET /students/{student_id}/lesson-reminders/pending.

Позволяет TG_LMS student-боту (сервисный X-API-Key, общий на всех учеников)
прочитать `lesson_reminder`-события конкретного ученика из inbox `Notifications`
(tsk-429), чтобы продублировать напоминание в Telegram — без нового вебхука,
read-only поверх существующей таблицы.

До этого эндпоинта service-key бот не мог получить per-ученический список:
`/me/notifications` требует `require_authenticated` и отвергает сервисный токен
(403), а legacy `/api/v1/notifications/` не фильтрует по user_id/kind вовсе.

Identity-гейт — тот же приём, что в `messages_extra.py::get_messages_for_user`
(explicit `user_id` в query + `current_user.is_service` bypass), а не
role-гейт `methodist/escalations/pending` — тот привязан к `current_user.id`,
что делает его непригодным для сервисного токена, читающего ЗА разных учеников
одним и тем же ключом (см. разведку tsk-431).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser

router = APIRouter(prefix="/students", tags=["student_lesson_reminders"])
logger = logging.getLogger("api.student_lesson_reminders")


class StudentLessonReminderItem(BaseModel):
    """Элемент списка pending lesson_reminder для ученика."""
    id: int
    created_at: datetime
    kind: str
    title: Optional[str] = None
    payload: dict
    read_at: Optional[datetime] = None


class StudentLessonReminderPendingResponse(BaseModel):
    """Ответ /students/{student_id}/lesson-reminders/pending."""
    items: list[StudentLessonReminderItem]
    count: int = Field(..., description="Длина items (≤ limit)")


@router.get(
    "/{student_id}/lesson-reminders/pending",
    response_model=StudentLessonReminderPendingResponse,
    status_code=status.HTTP_200_OK,
    summary="Pending lesson_reminder для ученика (tsk-431)",
    responses={
        200: {"description": "Список (возможно пустой)"},
        401: {"description": "Не аутентифицирован"},
        403: {"description": "Не свой user_id и не сервисный токен"},
    },
)
async def list_pending_lesson_reminders(
    student_id: int,
    since: Optional[datetime] = Query(
        None,
        description="Если указано — только события с created_at >= since (ISO8601)",
    ),
    limit: int = Query(100, ge=1, le=500),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> StudentLessonReminderPendingResponse:
    """Read-only. Используется TG_LMS student-поллером (tsk-431).

    Kind жёстко зафиксирован на `lesson_reminder` — эндпоинт называется по
    задаче, которую решает, не общий inbox-proxy (для этого есть
    `/me/notifications`, недоступный сервисному токену).
    """
    if not current_user.is_service and current_user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    params: dict = {"uid": student_id, "limit": int(limit)}
    since_clause = ""
    if since is not None:
        since_clause = "AND n.modified_at >= :since "
        params["since"] = since

    res = await db.execute(
        text(
            "SELECT n.id, n.modified_at, n.kind, n.title, n.payload, n.read_at "
            "FROM notifications n "
            "WHERE n.user_id = :uid "
            "  AND n.kind = 'lesson_reminder' "
            f"  {since_clause}"  # nosec B608 — since_clause из закрытого набора (либо "", либо литерал с :since bind)
            "ORDER BY n.modified_at ASC "
            "LIMIT :limit"
        ),
        params,
    )
    rows = res.fetchall()
    items = [
        StudentLessonReminderItem(
            id=int(r[0]),
            created_at=r[1],
            kind=str(r[2]),
            title=str(r[3]) if r[3] is not None else None,
            payload=dict(r[4]) if r[4] is not None else {},
            read_at=r[5],
        )
        for r in rows
    ]
    return StudentLessonReminderPendingResponse(items=items, count=len(items))
