"""API «лента активности учеников» для преподавателя (tsk-408).

``GET /api/v1/teacher/activity-feed``

Единый поток последних событий по ВСЕМ ученикам преподавателя — решение
задания (успешно/неуспешно/на проверке), запрос помощи, изучение материала —
отсортированный по времени (убывание), топ-``limit``. Не листать каждого
ученика отдельно: преподаватель видит поток происходящего сразу.

Отличие от ``tsk-303`` (единый inbox преподавателя, backlog): inbox — то, что
требует ДЕЙСТВИЯ учителя (непрочитанное, ждёт ответа); эта лента — просто
ПОТОК происходящего, без обязательства реагировать.

Гейт: роль ``teacher``/``methodist``/``admin`` (или сервисный токен). ACL —
тот же принцип, что у правки прогресса (``can_edit_progress``, tsk-297):
teacher видит события только своих учеников или учеников на закреплённых за
ним курсах; bypass у methodist/admin.

Read-only: ни одной записи в БД.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.activity_feed import (
    ActivityFeedEvent,
    ActivityFeedResponse,
    FeedStudent,
    FeedStudentsResponse,
)
from app.services import lesson_plan_service, teacher_activity_feed_service

logger = logging.getLogger("api.teacher_activity_feed")

router = APIRouter(tags=["teacher_activity_feed"])

_GATE = require_role("teacher", "methodist", "admin")


@router.get(
    "/teacher/activity-feed",
    response_model=ActivityFeedResponse,
    summary="Лента активности учеников преподавателя (решение заданий, помощь, материалы)",
)
async def get_teacher_activity_feed(
    limit: int = Query(100, ge=1, le=200, description="Размер страницы"),
    before: Optional[datetime] = Query(
        None, description="Курсор пагинации — только события строго раньше этого момента"
    ),
    student_id: Optional[int] = Query(
        None, ge=1, description="tsk-1362: только события этого ученика (в пределах ACL)"
    ),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_GATE),
) -> ActivityFeedResponse:
    """Топ-``limit`` событий по всем ученикам преподавателя, по убыванию времени."""
    events, has_more, next_before = await teacher_activity_feed_service.get_activity_feed(
        db, current_user, limit=limit, before=before, student_id=student_id,
    )
    return ActivityFeedResponse(
        events=[ActivityFeedEvent(**e) for e in events],
        has_more=has_more,
        next_before=next_before,
        on_lesson_student_ids=await lesson_plan_service.students_on_lesson_now(
            db, teacher_id=current_user.id,
        ),
    )


@router.get(
    "/teacher/activity-feed/students",
    response_model=FeedStudentsResponse,
    summary="Ученики для фильтра ленты: все в зоне преподавателя, «на занятии» сверху",
)
async def get_teacher_activity_feed_students(
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_GATE),
) -> FeedStudentsResponse:
    """tsk-1362: список учеников без привязки к загруженным событиям.

    Два лёгких запроса: кто на занятии (как в самой ленте) и один запрос ростера.
    """
    on_lesson = await lesson_plan_service.students_on_lesson_now(db, teacher_id=current_user.id)
    rows = await teacher_activity_feed_service.list_feed_students(
        db, current_user, on_lesson_ids=on_lesson,
    )
    return FeedStudentsResponse(students=[FeedStudent(**r) for r in rows])
