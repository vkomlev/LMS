"""
Сервис admin-CRUD Календаря LMS Фаза 1 (tsk-428): часы работы школы + слоты.

Бизнес-валидация (существование пользователей, ролей, пересечения слотов) —
здесь; прямой доступ к БД — в `app.repos.lesson_calendar_repository`.
Модель и границы MVP — docs/specs/2026-07-26-plan-kalendar-lms.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_slot import LessonSlot
from app.models.operating_hours import OperatingHours
from app.repos.lesson_calendar_repository import (
    LessonSlotRepository,
    OperatingHoursRepository,
)
from app.services import roles_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_operating_hours_repo = OperatingHoursRepository()
_lesson_slot_repo = LessonSlotRepository()


async def ensure_user_has_role(db: AsyncSession, user_id: int, expected_role: str) -> None:
    """404, если пользователя нет; 422, если у него нет ожидаемой роли."""
    from app.models.users import Users  # noqa: PLC0415 — избегаем circular import

    user = await db.get(Users, user_id)
    if user is None:
        raise DomainError(f"Пользователь id={user_id} не найден", status_code=404)

    role_names = await roles_service.get_user_role_names(db, user_id)
    if expected_role not in role_names:
        raise DomainError(
            f"Пользователь id={user_id} не имеет роли '{expected_role}' "
            f"(текущие роли: {role_names or 'нет'})",
            status_code=422,
        )


# ─── Operating Hours ────────────────────────────────────────────────────────


async def list_operating_hours(db: AsyncSession) -> list[OperatingHours]:
    return await _operating_hours_repo.list_all(db)


async def upsert_operating_hours(
    db: AsyncSession,
    *,
    weekday: int,
    start_time: time,
    end_time: time,
    timezone: str,
) -> OperatingHours:
    """Одна запись на weekday — повторный вызов для того же дня заменяет её
    (простое upsert-поведение, без отдельного PATCH по id для этой сущности,
    т.к. дней недели всего 7 и конфликтов идентификаторов быть не может)."""
    existing = await _operating_hours_repo.get_by_weekday(db, weekday)
    if existing is not None:
        await _operating_hours_repo.delete(db, existing)
        await db.flush()
    row = await _operating_hours_repo.create(
        db,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        timezone=timezone,
    )
    await db.commit()
    await db.refresh(row)
    return row


# ─── Lesson Slot ────────────────────────────────────────────────────────────


async def create_lesson_slot(
    db: AsyncSession,
    *,
    student_id: int,
    teacher_id: int,
    weekday: int,
    start_time: time,
    duration_minutes: int,
    timezone: str,
    created_by: Optional[int],
) -> LessonSlot:
    await ensure_user_has_role(db, student_id, "student")
    await ensure_user_has_role(db, teacher_id, "teacher")

    overlap = await _lesson_slot_repo.has_overlap(
        db,
        teacher_id=teacher_id,
        student_id=student_id,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
    )
    if overlap:
        raise DomainError(
            "Слот пересекается по времени с существующим активным слотом "
            "ученика или преподавателя в этот день недели",
            status_code=409,
        )

    row = await _lesson_slot_repo.create(
        db,
        student_id=student_id,
        teacher_id=teacher_id,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
        timezone=timezone,
        created_by=created_by,
    )
    await db.commit()
    await db.refresh(row)
    logger.info(
        "lesson_slot создан: id=%s student=%s teacher=%s weekday=%s start=%s",
        row.id, student_id, teacher_id, weekday, start_time,
    )
    return row


async def list_lesson_slots(
    db: AsyncSession,
    *,
    teacher_id: Optional[int] = None,
    student_id: Optional[int] = None,
) -> list[LessonSlot]:
    return await _lesson_slot_repo.list_active(db, teacher_id=teacher_id, student_id=student_id)


async def get_lesson_slot(db: AsyncSession, slot_id: int) -> LessonSlot:
    row = await _lesson_slot_repo.get_by_id(db, slot_id)
    if row is None:
        raise DomainError(f"Слот id={slot_id} не найден", status_code=404)
    return row


async def update_lesson_slot(
    db: AsyncSession,
    slot_id: int,
    *,
    weekday: Optional[int] = None,
    start_time: Optional[time] = None,
    duration_minutes: Optional[int] = None,
    timezone: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> LessonSlot:
    row = await get_lesson_slot(db, slot_id)

    new_weekday = weekday if weekday is not None else row.weekday
    new_start_time = start_time if start_time is not None else row.start_time
    new_duration = duration_minutes if duration_minutes is not None else row.duration_minutes

    reactivating_or_moving = any(
        v is not None for v in (weekday, start_time, duration_minutes)
    ) or (is_active is True and not row.is_active)
    if reactivating_or_moving and (is_active is not False):
        overlap = await _lesson_slot_repo.has_overlap(
            db,
            teacher_id=row.teacher_id,
            student_id=row.student_id,
            weekday=new_weekday,
            start_time=new_start_time,
            duration_minutes=new_duration,
            exclude_slot_id=row.id,
        )
        if overlap:
            raise DomainError(
                "Новое время слота пересекается с существующим активным слотом",
                status_code=409,
            )

    if weekday is not None:
        row.weekday = weekday
    if start_time is not None:
        row.start_time = start_time
    if duration_minutes is not None:
        row.duration_minutes = duration_minutes
    if timezone is not None:
        row.timezone = timezone
    if is_active is not None:
        row.is_active = is_active

    await db.commit()
    await db.refresh(row)
    return row


async def deactivate_lesson_slot(db: AsyncSession, slot_id: int) -> None:
    """Деактивация вместо удаления — сохраняет историю occurrence (см. спек)."""
    row = await get_lesson_slot(db, slot_id)
    row.is_active = False
    await db.commit()


# ─── Operating Hours check (используется Фазой 3: ad-hoc/reschedule) ────────


async def is_within_operating_hours(
    db: AsyncSession, *, scheduled_at: datetime, duration_minutes: int
) -> Optional[bool]:
    """Попадает ли занятие [scheduled_at, scheduled_at+duration) в часы
    работы школы этого дня недели.

    :returns: True/False — если для этого дня недели есть настроенная запись
        `operating_hours`; ``None`` — если `operating_hours` вообще не
        настроены (ни одной строки в таблице) — в этом случае вызывающий
        код НЕ должен блокировать операцию (осознанный graceful default:
        MVP/dev без сконфигурированных часов работы не должен запирать
        ad-hoc/reschedule).
    """
    rows = await _operating_hours_repo.list_all(db)
    if not rows:
        return None

    for row in rows:
        tz = ZoneInfo(row.timezone)
        local_dt = scheduled_at.astimezone(tz)
        if local_dt.weekday() != row.weekday:
            continue
        local_time = local_dt.time()
        local_end_dt = local_dt + timedelta(minutes=duration_minutes)
        if local_end_dt.date() != local_dt.date():
            # Занятие переходит через полночь операционного дня — вне часов
            # работы (MVP не поддерживает ночные занятия).
            continue
        if row.start_time <= local_time and local_end_dt.time() <= row.end_time:
            return True
    return False
