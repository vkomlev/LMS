"""API помощника вёрстки расписания (tsk-674, фаза 2).

- ``GET  /methodist/schedule-plan``         — спрос по часам, ученики, нынешние слоты
- ``POST /methodist/schedule-plan/preview`` — расчёт по набору часов (или предложение)
- ``POST /methodist/schedule-plan/apply``   — применить утверждённую сетку

Гейт тот же, что у расписания и у охвата опроса: `methodist`/`admin`. Слот —
распорядительное решение, преподаватель сюда не входит (см. `lesson_calendar_admin`).

Разделение «посчитать» и «применить» намеренное: считать можно сколько угодно,
а применение меняет расписание живым людям и всегда начинается с `dry_run`.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.schedule_plan import (
    SchedulePlanApplyRequest,
    SchedulePlanApplyResult,
    SchedulePlanPreview,
    SchedulePlanPreviewRequest,
    SchedulePlanSnapshot,
)
from app.services import schedule_plan_service

router = APIRouter(tags=["schedule_plan"])

_PLAN_GATE = require_role("methodist", "admin")


@router.get("/methodist/schedule-plan", response_model=SchedulePlanSnapshot)
async def get_schedule_plan(
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_PLAN_GATE),
) -> SchedulePlanSnapshot:
    """Спрос по часам, пожелания учеников и нынешние слоты — основа вёрстки."""
    return await schedule_plan_service.get_snapshot(db)


@router.post("/methodist/schedule-plan/preview", response_model=SchedulePlanPreview)
async def preview_schedule_plan(
    body: SchedulePlanPreviewRequest = Body(default_factory=SchedulePlanPreviewRequest),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_PLAN_GATE),
) -> SchedulePlanPreview:
    """Что получится по этому набору часов и чего это стоит.

    `hours=null` — попросить сервер подобрать набор самому. Это предложение:
    методист правит его руками и зовёт расчёт снова.
    """
    hours = (
        None
        if body.hours is None
        else [(h.weekday, h.start_time) for h in body.hours]
    )
    return await schedule_plan_service.preview(
        db, hours=hours, keep_existing=body.keep_existing
    )


@router.post("/methodist/schedule-plan/apply", response_model=SchedulePlanApplyResult)
async def apply_schedule_plan(
    body: SchedulePlanApplyRequest = Body(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PLAN_GATE),
) -> SchedulePlanApplyResult:
    """Применить сетку: создать слоты и разложить по ним учеников.

    С `dry_run=true` (умолчание) ничего не меняется — возвращается тот же отчёт.
    Экран показывает его человеку и только после подтверждения зовёт применение.
    """
    # `DomainError` наверх не ловим: в `app/api/main.py` для него есть общий
    # обработчик — он отдаёт тот же код и то же тело, что и остальным API.
    return await schedule_plan_service.apply_plan(
        db,
        body,
        actor_id=None if current_user.is_service else current_user.id,
    )
