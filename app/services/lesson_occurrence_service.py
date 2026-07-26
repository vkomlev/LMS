"""
Сервис Календаря LMS Фаза 3 (tsk-430): панель преподавателя, ручное
добавление ученика, перенос и отработка вне расписания.

Модель и границы MVP — docs/specs/2026-07-26-plan-kalendar-lms.md § «Фаза 3».
Переиспользует `ensure_user_has_role` и `is_within_operating_hours` из
`lesson_calendar_service` (Фаза 1) и коллизии из `LessonOccurrenceRepository`
(добавлены здесь же Фазой 3 — реальный диапазон времени, не weekday+time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceRepository,
    OperatingHoursRepository,
)
from app.services import audit_service, lesson_calendar_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_occurrence_repo = LessonOccurrenceRepository()
_operating_hours_repo = OperatingHoursRepository()

# Статусы, при которых occurrence уже структурно закрыт для reschedule/ownership-операций.
_LOCKED_STATUSES = frozenset({"no_show", "completed", "rescheduled"})

# Шаг перебора кандидатов для available-slots — компромисс между точностью
# и объёмом кандидатов; занятия обычно начинаются на круглые полчаса.
_SLOT_STEP_MINUTES = 30


# ─── Teacher panel ──────────────────────────────────────────────────────────


async def list_for_teacher(
    db: AsyncSession,
    *,
    teacher_id: int,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 100,
    no_show_threshold_minutes: int = 10,
) -> list[tuple[LessonOccurrence, bool]]:
    """Занятия преподавателя + флаг `is_overdue` (живой расчёт, не ждёт
    следующего cron-тика `lesson_attendance_cron_tick`): `status='scheduled'`
    и порог опоздания уже истёк."""
    rows = await _occurrence_repo.list_for_teacher(
        db, teacher_id=teacher_id, from_dt=from_dt, to_dt=to_dt, limit=limit
    )
    now_utc = datetime.now(timezone.utc)
    threshold = timedelta(minutes=no_show_threshold_minutes)
    result: list[tuple[LessonOccurrence, bool]] = []
    for row in rows:
        is_overdue = row.status == "scheduled" and (row.scheduled_at + threshold) < now_utc
        result.append((row, is_overdue))
    return result


async def get_occurrence_for_teacher(
    db: AsyncSession, *, occurrence_id: int, teacher_id: int
) -> LessonOccurrence:
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    if occurrence.teacher_id != teacher_id:
        raise DomainError("Занятие принадлежит другому преподавателю", status_code=403)
    return occurrence


_TEACHER_ACTION_TO_STATUS = {
    "manual_present": "confirmed",
    "manual_absent": "no_show",
}


async def record_teacher_attendance(
    db: AsyncSession,
    *,
    occurrence_id: int,
    teacher_id: int,
    action: str,
    ip: Optional[str] = None,
) -> LessonOccurrence:
    """Ручная отметка преподавателем. В отличие от студенческого
    `lesson_attendance_service.record_attendance`, здесь заблокирован только
    `rescheduled` (occurrence уже заменён другим) — `no_show`/`completed`
    преподаватель обязан уметь исправить вручную (например, система
    ошибочно пометила no_show, а ученик на самом деле пришёл)."""
    occurrence = await get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    if occurrence.status == "rescheduled":
        raise DomainError(
            "Занятие перенесено на другое — правьте актуальный occurrence "
            f"(rescheduled_to_id={occurrence.rescheduled_to_id})",
            status_code=409,
        )

    new_status = _TEACHER_ACTION_TO_STATUS[action]

    await db.execute(
        text(
            "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
            "VALUES (:oid, :uid, :action)"
        ),
        {"oid": occurrence.id, "uid": teacher_id, "action": action},
    )
    occurrence.status = new_status
    occurrence.updated_at = datetime.now(timezone.utc)

    await audit_service.log_event(
        db,
        audit_service.STUDENT_LESSON_ATTENDANCE_RECORDED,
        user_id=teacher_id,
        ip=ip,
        details={
            "occurrence_id": occurrence.id,
            "action": action,
            "new_status": new_status,
            "actor_role": "teacher",
        },
    )

    await db.commit()
    await db.refresh(occurrence)
    return occurrence


# ─── Ad-hoc creation (используется и teacher add-student, и student ad-hoc) ─


async def create_ad_hoc_occurrence(
    db: AsyncSession,
    *,
    student_id: int,
    teacher_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
) -> LessonOccurrence:
    """Создать occurrence вне регулярного расписания (`slot_id=NULL`).

    Используется двумя путями: ученик сам записывается на отработку
    (`POST /lesson-occurrences/ad-hoc`) и преподаватель добавляет ученика на
    занятие вручную (`POST /teacher/lesson-occurrences/add-student`).

    :raises DomainError: 404/422 — участник не найден/без нужной роли;
        422 — вне часов работы школы (если `operating_hours` настроены);
        409 — пересечение по времени с другим активным occurrence
        преподавателя или ученика.
    """
    await lesson_calendar_service.ensure_user_has_role(db, student_id, "student")
    await lesson_calendar_service.ensure_user_has_role(db, teacher_id, "teacher")

    within_hours = await lesson_calendar_service.is_within_operating_hours(
        db, scheduled_at=scheduled_at, duration_minutes=duration_minutes
    )
    if within_hours is False:
        raise DomainError(
            "Время вне часов работы школы (operating_hours)", status_code=422
        )

    overlap = await _occurrence_repo.has_overlap(
        db,
        teacher_id=teacher_id,
        student_id=student_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием ученика или "
            "преподавателя", status_code=409,
        )

    occurrence = await _occurrence_repo.create(
        db,
        slot_id=None,
        student_id=student_id,
        teacher_id=teacher_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        status="scheduled",
    )
    await db.commit()
    await db.refresh(occurrence)
    logger.info(
        "lesson_occurrence ad-hoc создан: id=%s student=%s teacher=%s at=%s",
        occurrence.id, student_id, teacher_id, scheduled_at,
    )
    return occurrence


# ─── Reschedule + available slots (студент) ────────────────────────────────


async def _get_occurrence_for_student_reschedule(
    db: AsyncSession, *, occurrence_id: int, student_id: int
) -> LessonOccurrence:
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    if occurrence.student_id != student_id:
        raise DomainError("Занятие принадлежит другому ученику", status_code=403)
    if occurrence.status in _LOCKED_STATUSES:
        raise DomainError(
            f"Занятие уже в статусе '{occurrence.status}' — перенос недоступен",
            status_code=409,
        )
    return occurrence


async def list_available_slots(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    limit: int = 10,
    horizon_days: int = 14,
) -> list[datetime]:
    """Кандидаты для переноса: в рамках `operating_hours`, без коллизий у
    преподавателя ИЛИ ученика. Пустой список — `operating_hours` не
    настроены (см. `is_within_operating_hours`) либо кандидатов не нашлось
    в пределах горизонта."""
    occurrence = await _get_occurrence_for_student_reschedule(
        db, occurrence_id=occurrence_id, student_id=student_id
    )

    hours_rows = await _operating_hours_repo.list_all(db)
    if not hours_rows:
        return []

    duration = occurrence.duration_minutes
    now_utc = datetime.now(timezone.utc)
    candidates: list[datetime] = []

    for day_offset in range(1, horizon_days + 1):
        if len(candidates) >= limit:
            break
        day = (now_utc + timedelta(days=day_offset)).date()
        for hours_row in hours_rows:
            tz = ZoneInfo(hours_row.timezone)
            cursor_local = datetime.combine(day, hours_row.start_time, tzinfo=tz)
            if cursor_local.weekday() != hours_row.weekday:
                continue

            end_local = datetime.combine(day, hours_row.end_time, tzinfo=tz)
            while cursor_local + timedelta(minutes=duration) <= end_local:
                candidate_utc = cursor_local.astimezone(timezone.utc)
                if candidate_utc > now_utc:
                    overlap = await _occurrence_repo.has_overlap(
                        db,
                        teacher_id=occurrence.teacher_id,
                        student_id=occurrence.student_id,
                        scheduled_at=candidate_utc,
                        duration_minutes=duration,
                        exclude_occurrence_id=occurrence.id,
                    )
                    if not overlap:
                        candidates.append(candidate_utc)
                        if len(candidates) >= limit:
                            break
                cursor_local += timedelta(minutes=_SLOT_STEP_MINUTES)

    return candidates[:limit]


async def reschedule_occurrence(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    new_scheduled_at: datetime,
) -> LessonOccurrence:
    """Перенести занятие: старый occurrence → `status=rescheduled` +
    `rescheduled_to_id`, создаётся новый (`slot_id=NULL`, тот же student/
    teacher/duration, новое время, `status=scheduled`).

    Без `attendance_event` для самого переноса — CHECK-constraint
    `attendance_event.action` не включает `rescheduled` (это состояние
    occurrence, не действие явки); полная провенанс — `rescheduled_to_id` +
    смена `status` на старой записи.
    """
    occurrence = await _get_occurrence_for_student_reschedule(
        db, occurrence_id=occurrence_id, student_id=student_id
    )

    within_hours = await lesson_calendar_service.is_within_operating_hours(
        db, scheduled_at=new_scheduled_at, duration_minutes=occurrence.duration_minutes
    )
    if within_hours is False:
        raise DomainError(
            "Новое время вне часов работы школы (operating_hours)", status_code=422
        )

    overlap = await _occurrence_repo.has_overlap(
        db,
        teacher_id=occurrence.teacher_id,
        student_id=occurrence.student_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Новое время пересекается с другим активным занятием", status_code=409
        )

    new_occurrence = await _occurrence_repo.create(
        db,
        slot_id=None,
        student_id=occurrence.student_id,
        teacher_id=occurrence.teacher_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        status="scheduled",
    )
    await db.flush()

    occurrence.status = "rescheduled"
    occurrence.rescheduled_to_id = new_occurrence.id
    occurrence.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(new_occurrence)
    logger.info(
        "lesson_occurrence перенесён: old=%s new=%s student=%s at=%s",
        occurrence.id, new_occurrence.id, student_id, new_scheduled_at,
    )
    return new_occurrence
