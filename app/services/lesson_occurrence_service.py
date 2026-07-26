"""
Сервис Календаря LMS (tsk-430/435): панель преподавателя, ручное добавление
участника, перенос и отработка вне расписания — всё per-участнику (групповое
occurrence, tsk-435).

Модель и границы — docs/specs/2026-07-26-plan-kalendar-lms.md § «Фаза 3» +
tsk-435 (rework на группы). Переиспользует `ensure_user_has_role` и
`is_within_operating_hours` из `lesson_calendar_service`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceParticipantRepository,
    LessonOccurrenceRepository,
    OperatingHoursRepository,
)
from app.services import audit_service, lesson_calendar_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_occurrence_repo = LessonOccurrenceRepository()
_participant_repo = LessonOccurrenceParticipantRepository()
_operating_hours_repo = OperatingHoursRepository()

# Статусы, при которых участие уже структурно закрыто для reschedule/ownership-операций.
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
) -> list[tuple[LessonOccurrence, list[tuple[LessonOccurrenceParticipant, bool]]]]:
    """Занятия преподавателя, каждое — с полным списком участников + флаг
    `is_overdue` НА КАЖДОГО (живой расчёт, не ждёт cron-тик): участник в
    `status='scheduled'` и порог опоздания уже истёк."""
    occurrences = await _occurrence_repo.list_for_teacher(
        db, teacher_id=teacher_id, from_dt=from_dt, to_dt=to_dt, limit=limit
    )
    if not occurrences:
        return []

    occurrence_ids = [o.id for o in occurrences]
    all_participants = await _participant_repo.list_for_occurrences(db, occurrence_ids)
    participants_by_occurrence: dict[int, list[LessonOccurrenceParticipant]] = {}
    for p in all_participants:
        participants_by_occurrence.setdefault(p.occurrence_id, []).append(p)

    now_utc = datetime.now(timezone.utc)
    threshold = timedelta(minutes=no_show_threshold_minutes)

    result: list[tuple[LessonOccurrence, list[tuple[LessonOccurrenceParticipant, bool]]]] = []
    for occurrence in occurrences:
        participants = participants_by_occurrence.get(occurrence.id, [])
        pairs = [
            (
                p,
                p.status == "scheduled" and (occurrence.scheduled_at + threshold) < now_utc,
            )
            for p in participants
        ]
        result.append((occurrence, pairs))
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
    student_id: int,
    action: str,
    ip: Optional[str] = None,
) -> LessonOccurrenceParticipant:
    """Ручная отметка преподавателем ОДНОГО участника occurrence. В отличие
    от студенческого `lesson_attendance_service.record_attendance`, здесь
    заблокирован только `rescheduled` (участие уже заменено другим) —
    `no_show`/`completed` преподаватель обязан уметь исправить вручную."""
    occurrence = await get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    participant = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if participant is None:
        raise DomainError(
            f"Ученик id={student_id} не входит в число участников этого занятия",
            status_code=404,
        )
    if participant.status == "rescheduled":
        raise DomainError(
            "Участие перенесено на другое занятие — правьте актуальный occurrence "
            f"(rescheduled_to_occurrence_id={participant.rescheduled_to_occurrence_id})",
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
    participant.status = new_status
    participant.updated_at = datetime.now(timezone.utc)

    await audit_service.log_event(
        db,
        audit_service.STUDENT_LESSON_ATTENDANCE_RECORDED,
        user_id=teacher_id,
        ip=ip,
        details={
            "occurrence_id": occurrence.id,
            "student_id": student_id,
            "action": action,
            "new_status": new_status,
            "actor_role": "teacher",
        },
    )

    await db.commit()
    await db.refresh(participant)
    return participant


# ─── Ad-hoc creation + add-participant (teacher/student) ───────────────────


async def create_ad_hoc_occurrence(
    db: AsyncSession,
    *,
    student_id: int,
    teacher_id: int,
    scheduled_at: datetime,
    duration_minutes: int,
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Создать occurrence вне регулярного расписания (`slot_id=NULL`) с одним
    начальным участником. Используется двумя путями: ученик сам записывается
    на отработку (`POST /lesson-occurrences/ad-hoc`) и преподаватель добавляет
    ученика вручную (`POST /teacher/lesson-occurrences/add-student`).

    Коллизия проверяется только по УЧЕНИКУ (не по преподавателю — групповое
    occurrence по design допускает несколько параллельных occurrence у одного
    преподавателя).

    :raises DomainError: 404/422 — участник не найден/без нужной роли;
        422 — вне часов работы школы (если `operating_hours` настроены);
        409 — пересечение по времени с другим активным участием ученика.
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

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    occurrence = await _occurrence_repo.create(
        db,
        slot_id=None,
        teacher_id=teacher_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    await db.flush()
    participant = await _participant_repo.create(
        db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
    )
    await db.commit()
    await db.refresh(occurrence)
    await db.refresh(participant)
    logger.info(
        "lesson_occurrence ad-hoc создан: id=%s student=%s teacher=%s at=%s",
        occurrence.id, student_id, teacher_id, scheduled_at,
    )
    return occurrence, participant


async def add_participant_to_occurrence(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    teacher_id: int,
) -> LessonOccurrenceParticipant:
    """Добавить ученика к УЖЕ существующему occurrence (например, подключить
    опоздавшего/новенького к уже идущей группе). Идемпотентно: уже
    участвующий ученик возвращает текущую строку."""
    occurrence = await get_occurrence_for_teacher(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    await lesson_calendar_service.ensure_user_has_role(db, student_id, "student")

    existing = await _participant_repo.get(
        db, occurrence_id=occurrence_id, student_id=student_id
    )
    if existing is not None:
        return existing

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=occurrence.scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    participant = await _participant_repo.create(
        db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
    )
    await db.commit()
    await db.refresh(participant)
    return participant


# ─── Reschedule + available slots (студент, по своему участию) ─────────────


async def _get_own_participant_for_reschedule(
    db: AsyncSession, *, occurrence_id: int, student_id: int
) -> tuple[LessonOccurrenceParticipant, LessonOccurrence]:
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
            f"Участие уже в статусе '{participant.status}' — перенос недоступен",
            status_code=409,
        )
    return participant, occurrence


async def list_available_slots(
    db: AsyncSession,
    *,
    occurrence_id: int,
    student_id: int,
    limit: int = 10,
    horizon_days: int = 14,
) -> list[datetime]:
    """Кандидаты для переноса: в рамках `operating_hours`, без коллизий у
    ЭТОГО ученика (преподаватель по design может вести несколько occurrence
    одновременно — групповое расписание). Пустой список — `operating_hours`
    не настроены либо кандидатов не нашлось в пределах горизонта."""
    participant, occurrence = await _get_own_participant_for_reschedule(
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
                    overlap = await _participant_repo.has_student_overlap(
                        db,
                        student_id=student_id,
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
) -> tuple[LessonOccurrence, LessonOccurrenceParticipant]:
    """Перенести УЧАСТИЕ этого ученика: старая строка участника →
    `status=rescheduled` + `rescheduled_to_occurrence_id`, создаётся НОВЫЙ
    occurrence (`slot_id=NULL`, тот же teacher/duration, новое время) с
    новой строкой участника (`status=scheduled`). Остальные участники
    старого (группового) occurrence не затрагиваются — их перенос
    независим (см. модель tsk-435).

    Без `attendance_event` для самого переноса — CHECK-constraint
    `attendance_event.action` не включает `rescheduled` (это состояние
    участника, не действие явки); полная провенанс —
    `rescheduled_to_occurrence_id` + смена `status` на старой записи.
    """
    old_participant, occurrence = await _get_own_participant_for_reschedule(
        db, occurrence_id=occurrence_id, student_id=student_id
    )

    within_hours = await lesson_calendar_service.is_within_operating_hours(
        db, scheduled_at=new_scheduled_at, duration_minutes=occurrence.duration_minutes
    )
    if within_hours is False:
        raise DomainError(
            "Новое время вне часов работы школы (operating_hours)", status_code=422
        )

    overlap = await _participant_repo.has_student_overlap(
        db,
        student_id=student_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
        exclude_occurrence_id=occurrence.id,
    )
    if overlap:
        raise DomainError(
            "Новое время пересекается с другим активным занятием этого ученика",
            status_code=409,
        )

    new_occurrence = await _occurrence_repo.create(
        db,
        slot_id=None,
        teacher_id=occurrence.teacher_id,
        scheduled_at=new_scheduled_at,
        duration_minutes=occurrence.duration_minutes,
    )
    await db.flush()
    new_participant = await _participant_repo.create(
        db, occurrence_id=new_occurrence.id, student_id=student_id, status="scheduled",
    )
    await db.flush()

    old_participant.status = "rescheduled"
    old_participant.rescheduled_to_occurrence_id = new_occurrence.id
    old_participant.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(new_occurrence)
    await db.refresh(new_participant)
    logger.info(
        "lesson_occurrence участие перенесено: old_occ=%s new_occ=%s student=%s at=%s",
        occurrence.id, new_occurrence.id, student_id, new_scheduled_at,
    )
    return new_occurrence, new_participant
