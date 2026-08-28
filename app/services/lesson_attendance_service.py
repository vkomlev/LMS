"""
Сервис явки ученика (tsk-429/435, Календарь LMS): подтвердить/отказаться от
занятия + список предстоящих/прошедших occurrence ученика.

Групповое occurrence (tsk-435): статус живёт на участнике
(`lesson_occurrence_participant`), не на самом occurrence — у каждого
участника своя независимая явка. Ownership (IDOR): наличие СВОЕЙ строки
участника проверяется здесь, не в роутере.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.config import Settings
from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceParticipantRepository,
    LessonOccurrenceRepository,
)
from app.services import audit_service
from app.utils.exceptions import DomainError

_occurrence_repo = LessonOccurrenceRepository()
_participant_repo = LessonOccurrenceParticipantRepository()

# Статусы, в которых участие уже закрыто — явку через этот сервис менять нельзя.
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
) -> tuple[LessonOccurrenceParticipant, LessonOccurrence]:
    """Записать явку/отказ ученика. Идемпотентно: повторный тот же action —
    no-op (возвращает текущее состояние, событие всё равно логируется в
    ``attendance_event`` как факт повторного подтверждения).

    :raises DomainError: 404 — occurrence не найден; 403 — ученик не входит
        в число участников этого occurrence; 409 — участие уже в закрытом
        статусе.
    """
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)

    participant = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if participant is None:
        raise DomainError(
            "Ученик не входит в число участников этого занятия", status_code=403
        )

    if participant.status in _LOCKED_STATUSES:
        raise DomainError(
            f"Участие уже в статусе '{participant.status}' — явку изменить нельзя",
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
    participant.status = new_status
    participant.updated_at = datetime.now(timezone.utc)

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
    await db.refresh(participant)
    return participant, occurrence


async def list_student_occurrences(
    db: AsyncSession,
    *,
    student_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 50,
) -> list[tuple[LessonOccurrenceParticipant, LessonOccurrence]]:
    return await _participant_repo.list_for_student(
        db, student_id=student_id, from_dt=from_dt, to_dt=to_dt, limit=limit
    )


async def get_occurrence_for_student(
    db: AsyncSession, *, occurrence_id: int, student_id: int
) -> tuple[LessonOccurrenceParticipant, LessonOccurrence]:
    """404/403-safe чтение одного occurrence ученика через его участие."""
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    participant = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if participant is None:
        raise DomainError(
            "Ученик не входит в число участников этого занятия", status_code=403
        )
    return participant, occurrence


async def auto_confirm_if_in_progress(db: AsyncSession, *, student_id: int) -> bool:
    """tsk-439: если у ученика ПРЯМО СЕЙЧАС идёт занятие (участие ещё
    `scheduled`, время в пределах [scheduled_at, scheduled_at+duration)) —
    реальное учебное действие (сдача ответа/завершение материала)
    автоматически подтверждает явку, не дожидаясь явного клика "Я на
    занятии" (решение оператора: реальное учебное действие = явка).

    Тихий no-op (return False) в подавляющем большинстве вызовов — учебная
    активность почти всегда происходит вне времени занятий. Коммитит сам:
    у `attempts.py`/`learning.py` нет гарантированного финального commit
    после основной записи (там своя commit-логика внутри try/except веток),
    а явка — независимая по смыслу запись, ей не нужна строгая атомарность
    с task_results/student_material_progress. Вызывающий код оборачивает
    вызов в свой soft-fail try/except (см. 2.4b/2.4c в `attempts.py`).

    tsk-455: `early_grace_minutes` (настройка
    `lesson_auto_confirm_early_grace_minutes`) даёт запас ДО начала занятия —
    живой инцидент показал, что строгое "занятие уже началось" отсекало
    ученика, сдавшего ответ за 13 секунд до scheduled_at.
    """
    settings = Settings()
    participant = await _participant_repo.get_current_scheduled_for_student(
        db,
        student_id=student_id,
        now=datetime.now(timezone.utc),
        early_grace_minutes=settings_store.get_int(
            "lesson_auto_confirm_early_grace_minutes"
        ),
    )
    if participant is None:
        return False

    await db.execute(
        text(
            "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
            "VALUES (:oid, :uid, 'auto_joined')"
        ),
        {"oid": participant.occurrence_id, "uid": student_id},
    )
    participant.status = "confirmed"
    participant.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return True
