"""
Сервис явки ученика (tsk-429, Календарь LMS Фаза 2): подтвердить/отказаться
от занятия + список предстоящих/прошедших occurrence ученика.

Ownership (IDOR): все операции скоуплены по `student_id == current_user.id`,
проверяется здесь, не в роутере (роутер только резолвит CurrentUser).
Модель — docs/specs/2026-07-26-plan-kalendar-lms.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.repos.lesson_calendar_repository import LessonOccurrenceRepository
from app.services import audit_service
from app.utils.exceptions import DomainError

_occurrence_repo = LessonOccurrenceRepository()

# Статусы, в которых занятие уже закрыто — явку через этот сервис менять нельзя.
_LOCKED_STATUSES = frozenset({"no_show", "completed", "rescheduled"})

_ACTION_TO_STATUS = {
    "joined": "confirmed",
    "declined": "declined",
}


async def record_attendance(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    action: str,
    ip: Optional[str] = None,
) -> LessonOccurrence:
    """Записать явку/отказ ученика. Идемпотентно: повторный тот же action —
    no-op (возвращает текущее состояние, событие всё равно логируется в
    ``attendance_event`` как факт повторного подтверждения).

    :raises DomainError: 404 — occurrence не найден; 403 — принадлежит
        другому ученику; 409 — занятие уже в закрытом статусе.
    """
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)

    if occurrence.student_id != student_id:
        raise DomainError(
            "Занятие принадлежит другому ученику", status_code=403
        )

    if occurrence.status in _LOCKED_STATUSES:
        raise DomainError(
            f"Занятие уже в статусе '{occurrence.status}' — явку изменить нельзя",
            status_code=409,
        )

    new_status = _ACTION_TO_STATUS[action]

    await db.execute(
        text(
            "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
            "VALUES (:oid, :uid, :action)"
        ),
        {"oid": occurrence.id, "uid": student_id, "action": action},
    )
    occurrence.status = new_status
    occurrence.updated_at = datetime.now(timezone.utc)

    await audit_service.log_event(
        db,
        audit_service.STUDENT_LESSON_ATTENDANCE_RECORDED,
        user_id=student_id,
        ip=ip,
        details={
            "occurrence_id": occurrence.id,
            "action": action,
            "new_status": new_status,
        },
    )

    await db.commit()
    await db.refresh(occurrence)
    return occurrence


async def list_student_occurrences(
    db: AsyncSession,
    *,
    student_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 50,
) -> list[LessonOccurrence]:
    return await _occurrence_repo.list_for_student(
        db, student_id=student_id, from_dt=from_dt, to_dt=to_dt, limit=limit
    )


async def get_occurrence_for_student(
    db: AsyncSession, *, occurrence_id: int, student_id: int
) -> LessonOccurrence:
    """404/403-safe чтение одного occurrence ученика (используется API-слоем
    для отдачи актуального состояния после ошибки/для GET по id)."""
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    if occurrence.student_id != student_id:
        raise DomainError("Занятие принадлежит другому ученику", status_code=403)
    return occurrence
