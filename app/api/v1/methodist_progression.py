"""Ручные разблокировки жёсткой последовательности подкурсов (tsk-1500).

Методист видит, кто сейчас в обходе правила корневого курса, и может
отпустить конкретного ученика вперёд (болезнь, договорённость), не выключая
настройку для всех. Удаление записи возвращает ученика под правило.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, require_role
from app.auth.current_user import CurrentUser
from app.services.audit_service import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/methodist", tags=["methodist_progression"])

_GATE = require_role("methodist", "admin")

_IS_ROOT_SQL = "SELECT NOT EXISTS (SELECT 1 FROM course_parents WHERE course_id = :id)"


async def _require_human(current_user: CurrentUser = Depends(_GATE)) -> CurrentUser:
    """require_role пропускает сервисный токен без проверки роли — ради ботов.

    Разблокировка — решение человека (как перерывы в methodist_breaks): «кто
    отпустил ученика вперёд» — часть смысла записи, сервисному ключу здесь
    нечего делать. Поймано живым прогоном: id=0 сервисного ключа ломал FK
    audit_event и progression_overrides (tsk-1500).
    """
    if current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Разблокировки заводит пользователь (методист/админ), а не сервисный ключ",
        )
    return current_user


class OverrideRead(BaseModel):
    """Ученик в обходе правила курса."""

    user_id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime


class OverrideCreate(BaseModel):
    user_id: int = Field(..., description="ID ученика")


async def _require_course(db: AsyncSession, course_id: int) -> None:
    """Курс существует и является корневым (настройка живёт на корне)."""
    row = await db.execute(text("SELECT EXISTS (SELECT 1 FROM courses WHERE id = :id)"), {"id": course_id})
    if not row.scalar():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    is_root = await db.execute(text(_IS_ROOT_SQL), {"id": course_id})
    if not is_root.scalar():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Разблокировки живут на корневом курсе: привяжите к корню дерева",
        )


@router.get(
    "/courses/{course_id}/progression-overrides",
    response_model=List[OverrideRead],
    summary="Ученики в обходе жёсткой последовательности курса",
)
async def list_overrides(
    course_id: int,
    current_user: CurrentUser = Depends(_GATE),
    db: AsyncSession = Depends(get_bare_db),
) -> List[OverrideRead]:
    await _require_course(db, course_id)
    rows = await db.execute(
        text(
            "SELECT o.user_id, u.full_name, u.email, o.created_at "
            "FROM progression_overrides o JOIN users u ON u.id = o.user_id "
            "WHERE o.root_course_id = :id ORDER BY o.created_at DESC"
        ),
        {"id": course_id},
    )
    return [
        OverrideRead(
            user_id=int(r[0]),
            full_name=r[1],
            email=r[2],
            created_at=r[3],
        )
        for r in rows.fetchall()
    ]


@router.post(
    "/courses/{course_id}/progression-overrides",
    response_model=OverrideRead,
    status_code=status.HTTP_201_CREATED,
    summary="Отпустить ученика вперёд в обход жёсткой последовательности",
)
async def add_override(
    course_id: int,
    body: OverrideCreate,
    current_user: CurrentUser = Depends(_require_human),
    db: AsyncSession = Depends(get_bare_db),
) -> OverrideRead:
    await _require_course(db, course_id)
    user = await db.execute(
        text("SELECT id FROM users WHERE id = :id"), {"id": body.user_id}
    )
    if user.scalar() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    await db.execute(
        text(
            "INSERT INTO progression_overrides (root_course_id, user_id, created_by) "
            "VALUES (:root, :user, :by) "
            "ON CONFLICT (root_course_id, user_id) DO NOTHING"
        ),
        {"root": course_id, "user": body.user_id, "by": current_user.id},
    )
    await log_event(
        db,
        "progression_override_added",
        user_id=current_user.id,
        details={"course_id": course_id, "student_id": body.user_id},
    )
    await db.commit()
    row = await db.execute(
        text("SELECT full_name, email FROM users WHERE id = :id"), {"id": body.user_id}
    )
    full_name, email = row.one()
    logger.info(
        "strict_progression: методист %s разблокировал ученика %s на корне %s",
        current_user.id, body.user_id, course_id,
    )
    return OverrideRead(
        user_id=body.user_id, full_name=full_name, email=email, created_at=datetime.now()
    )


@router.delete(
    "/courses/{course_id}/progression-overrides/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Вернуть ученика под правило жёсткой последовательности",
)
async def remove_override(
    course_id: int,
    user_id: int,
    current_user: CurrentUser = Depends(_require_human),
    db: AsyncSession = Depends(get_bare_db),
) -> Response:
    await _require_course(db, course_id)
    result = await db.execute(
        text(
            "DELETE FROM progression_overrides "
            "WHERE root_course_id = :root AND user_id = :user"
        ),
        {"root": course_id, "user": user_id},
    )
    await log_event(
        db,
        "progression_override_removed",
        user_id=current_user.id,
        details={"course_id": course_id, "student_id": user_id},
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Разблокировка не найдена")
    logger.info(
        "strict_progression: методист %s вернул правило ученику %s на корне %s",
        current_user.id, user_id, course_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
