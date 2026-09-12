"""GET /teacher/attention/summary — сводка «требует внимания» (tsk-652).

Используется TG_LMS teacher-бот-поллером (`bots/teacher/poller.py`,
`poll_teacher_attention`) — тот же паттерн, что у
`/teacher/reviews/pending-count` и `/teacher/help-requests/pending-count`:
рост счётчика между тиками → push в Telegram. Не новый экран (он уже есть —
`/teacher/gap-signals`, `/me/notifications`), не новый вид уведомления —
только точка агрегации для доставки-хука.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.teacher_attention import TeacherAttentionSummaryResponse
from app.services import teacher_attention_service

router = APIRouter(prefix="/teacher/attention", tags=["teacher_attention"])
logger = logging.getLogger("api.teacher_attention")


@router.get(
    "/summary",
    response_model=TeacherAttentionSummaryResponse,
    summary="Сводка отклонений, требующих внимания преподавателя (tsk-652)",
)
async def attention_summary(
    teacher_id: int = Query(..., description="ID преподавателя"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> TeacherAttentionSummaryResponse:
    if not current_user.is_service and current_user.id != teacher_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    summary = await teacher_attention_service.get_summary(db, teacher_id=teacher_id)
    return TeacherAttentionSummaryResponse(**summary)
