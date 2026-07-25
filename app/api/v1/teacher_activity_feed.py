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
from app.schemas.activity_feed import ActivityFeedEvent, ActivityFeedResponse
from app.services import teacher_activity_feed_service

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
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_GATE),
) -> ActivityFeedResponse:
    """Топ-``limit`` событий по всем ученикам преподавателя, по убыванию времени."""
    events, has_more, next_before = await teacher_activity_feed_service.get_activity_feed(
        db, current_user, limit=limit, before=before,
    )
    return ActivityFeedResponse(
        events=[ActivityFeedEvent(**e) for e in events],
        has_more=has_more,
        next_before=next_before,
    )
