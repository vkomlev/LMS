"""API домашней работы (tsk-741, фаза 3).

Две стороны одного объекта:

* ``GET  /api/v1/me/homework``                                — что задали мне;
* ``GET  /api/v1/teacher/students/{id}/homework``             — что задано ученику;
* ``GET  /api/v1/teacher/students/{id}/homework/volume``      — что советует формула;
* ``POST /api/v1/teacher/students/{id}/homework``             — выдать;
* ``DELETE /api/v1/teacher/students/{id}/homework``           — отменить выдачу.

Гейт преподавательской стороны — тот же scoped-ACL, что у правки прогресса
(`ensure_can_edit_progress`): роли мало, преподаватель работает только со
своими учениками или с учениками закреплённых за ним курсов. Своего правила
доступа здесь не заводится — иначе домашняя работа однажды разойдётся с тем,
кому вообще можно трогать прогресс этого ученика.

Расчёт нормы отделён от выдачи намеренно: `/volume` ничего не пишет, и его
можно открыть, чтобы просто посмотреть, что система предлагает и почему.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_bare_db, require_authenticated, require_role
from app.auth.current_user import CurrentUser
from app.schemas.homework import (
    HomeworkIssueRequest,
    HomeworkRead,
    HomeworkVolumeRead,
)
from app.services import homework_service, homework_volume_service, manual_progress_service
from app.services.audit_service import log_event

logger = logging.getLogger("api.homework")

router = APIRouter(tags=["homework"])

_TEACHER_GATE = require_role("teacher", "methodist", "admin")
_BASE = "/teacher/students/{student_id}/homework"


# ── Ученик ──────────────────────────────────────────────────────────────────

@router.get("/me/homework", response_model=HomeworkRead | None)
async def get_my_homework(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> HomeworkRead | None:
    """Моя домашняя работа: состав, срок, что уже сделано.

    `null` — ничего не задано. Просроченную выдачу не прячем: невыполненное с
    прошедшим сроком ученик должен видеть, а не обнаруживать на занятии.

    401 (без auth) и 403 (сервисный токен) даёт `require_authenticated`.
    """
    homework = await homework_service.get_current(db, student_id=current_user.id)
    return HomeworkRead(**homework) if homework else None


# ── Преподаватель ───────────────────────────────────────────────────────────

@router.get(_BASE, response_model=HomeworkRead | None)
async def get_student_homework(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> HomeworkRead | None:
    """Что задано этому ученику и что из этого сделано."""
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)
    homework = await homework_service.get_current(db, student_id=student_id)
    return HomeworkRead(**homework) if homework else None


@router.get(
    _BASE + "/volume",
    response_model=HomeworkVolumeRead,
)
async def get_homework_volume(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> HomeworkVolumeRead:
    """Что советует формула — и из чего это число сложилось.

    Ничего не пишет и ничего не задаёт: преподаватель вправе сначала
    посмотреть, а решить потом. Здесь же видно `weeks_behind` — на сколько
    недель программа опаздывает при нынешнем темпе ученика.
    """
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)
    plan = await homework_volume_service.compute(db, student_id=student_id)
    return HomeworkVolumeRead(**plan.as_details())


@router.post(
    _BASE,
    response_model=HomeworkRead,
    status_code=status.HTTP_201_CREATED,
)
async def issue_student_homework(
    body: HomeworkIssueRequest,
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> HomeworkRead:
    """Выдать домашнюю работу. Предыдущая действующая выдача гасится.

    Состав собирается из следующих незавершённых элементов программы ученика в
    учебном порядке — материалы узла идут перед его заданиями, поэтому теория
    попадает домой сама собой.

    422 — срок в прошлом или в программе не осталось незавершённых элементов.
    """
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)
    now = datetime.now(timezone.utc)
    due_at = body.due_at or await homework_service.next_due_for(
        db, student_id=student_id, after=now, now=now
    )
    try:
        homework = await homework_service.issue(
            db,
            student_id=student_id,
            due_at=due_at,
            source="teacher",
            issued_by=current_user.id,
            volume_override=body.volume,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await log_event(
        db,
        "homework.issued",
        user_id=current_user.id,
        details={
            "student_id": student_id,
            "homework_id": homework["id"],
            "total": homework["total"],
            "planned_volume": homework["planned_volume"],
            "source": "teacher",
        },
    )
    await db.commit()
    return HomeworkRead(**homework)


@router.delete(_BASE, status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def cancel_student_homework(
    student_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_bare_db),
    current_user: CurrentUser = Depends(_TEACHER_GATE),
) -> None:
    """Отменить действующую выдачу. Идемпотентно: отменять нечего — тоже 204.

    Выдача не удаляется, а помечается отменённой: преподаватель должен видеть,
    что задавал, а прошлые счётчики не должны меняться задним числом.
    """
    await manual_progress_service.ensure_can_edit_progress(db, current_user, student_id)
    homework = await homework_service.get_current(db, student_id=student_id)
    if homework is None:
        return None
    await homework_service.cancel(db, homework_id=homework["id"])
    await log_event(
        db,
        "homework.cancelled",
        user_id=current_user.id,
        details={"student_id": student_id, "homework_id": homework["id"]},
    )
    await db.commit()
    return None
