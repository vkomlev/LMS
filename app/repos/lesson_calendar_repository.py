"""
Репозитории Календаря LMS Фаза 1-2 (tsk-428/tsk-429): operating_hours,
lesson_slot, lesson_occurrence.

Прямой доступ к БД, без бизнес-валидации — она в `lesson_calendar_service` /
`lesson_attendance_service`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_slot import LessonSlot
from app.models.operating_hours import OperatingHours


class OperatingHoursRepository:
    """CRUD часов работы школы (общие для всей школы, не per-teacher)."""

    async def list_all(self, db: AsyncSession) -> list[OperatingHours]:
        res = await db.execute(select(OperatingHours).order_by(OperatingHours.weekday))
        return list(res.scalars().all())

    async def get_by_weekday(self, db: AsyncSession, weekday: int) -> Optional[OperatingHours]:
        res = await db.execute(
            select(OperatingHours).where(OperatingHours.weekday == weekday)
        )
        return res.scalar_one_or_none()

    async def get_by_id(self, db: AsyncSession, row_id: int) -> Optional[OperatingHours]:
        return await db.get(OperatingHours, row_id)

    async def create(self, db: AsyncSession, **fields) -> OperatingHours:
        row = OperatingHours(**fields)
        db.add(row)
        await db.flush()
        return row

    async def delete(self, db: AsyncSession, row: OperatingHours) -> None:
        await db.delete(row)


class LessonSlotRepository:
    """CRUD закреплённых слотов расписания (индивидуальная пара ученик-преподаватель)."""

    async def get_by_id(self, db: AsyncSession, slot_id: int) -> Optional[LessonSlot]:
        return await db.get(LessonSlot, slot_id)

    async def list_active(
        self,
        db: AsyncSession,
        *,
        teacher_id: Optional[int] = None,
        student_id: Optional[int] = None,
    ) -> list[LessonSlot]:
        stmt = select(LessonSlot).where(LessonSlot.is_active.is_(True))
        if teacher_id is not None:
            stmt = stmt.where(LessonSlot.teacher_id == teacher_id)
        if student_id is not None:
            stmt = stmt.where(LessonSlot.student_id == student_id)
        res = await db.execute(stmt.order_by(LessonSlot.weekday, LessonSlot.start_time))
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> LessonSlot:
        row = LessonSlot(**fields)
        db.add(row)
        await db.flush()
        return row

    async def has_overlap(
        self,
        db: AsyncSession,
        *,
        teacher_id: int,
        student_id: int,
        weekday: int,
        start_time,
        duration_minutes: int,
        exclude_slot_id: Optional[int] = None,
    ) -> bool:
        """Есть ли у преподавателя ИЛИ ученика активный слот, пересекающийся
        по времени в этот день недели (простая проверка на пересечение
        интервалов на стороне Python, объём данных на пару — единицы строк)."""
        stmt = select(LessonSlot).where(
            LessonSlot.is_active.is_(True),
            LessonSlot.weekday == weekday,
            (LessonSlot.teacher_id == teacher_id) | (LessonSlot.student_id == student_id),
        )
        if exclude_slot_id is not None:
            stmt = stmt.where(LessonSlot.id != exclude_slot_id)
        res = await db.execute(stmt)
        existing = res.scalars().all()

        new_start = start_time.hour * 60 + start_time.minute
        new_end = new_start + duration_minutes
        for row in existing:
            row_start = row.start_time.hour * 60 + row.start_time.minute
            row_end = row_start + row.duration_minutes
            if new_start < row_end and row_start < new_end:
                return True
        return False


class LessonOccurrenceRepository:
    """Чтение конкретных занятий (create — только генератор/ad-hoc сервис)."""

    async def get_by_id(self, db: AsyncSession, occurrence_id: int) -> Optional[LessonOccurrence]:
        return await db.get(LessonOccurrence, occurrence_id)

    async def list_for_student(
        self,
        db: AsyncSession,
        *,
        student_id: int,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 50,
    ) -> list[LessonOccurrence]:
        stmt = select(LessonOccurrence).where(LessonOccurrence.student_id == student_id)
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at <= to_dt)
        stmt = stmt.order_by(LessonOccurrence.scheduled_at.asc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def list_for_teacher(
        self,
        db: AsyncSession,
        *,
        teacher_id: int,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[LessonOccurrence]:
        stmt = select(LessonOccurrence).where(LessonOccurrence.teacher_id == teacher_id)
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at <= to_dt)
        stmt = stmt.order_by(LessonOccurrence.scheduled_at.asc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> LessonOccurrence:
        row = LessonOccurrence(**fields)
        db.add(row)
        await db.flush()
        return row

    async def has_overlap(
        self,
        db: AsyncSession,
        *,
        teacher_id: int,
        student_id: int,
        scheduled_at: datetime,
        duration_minutes: int,
        exclude_occurrence_id: Optional[int] = None,
    ) -> bool:
        """Пересечение по РЕАЛЬНОМУ диапазону времени (не weekday+time-of-day,
        как у `LessonSlotRepository.has_overlap`) — для ad-hoc/reschedule.
        Занятые статусы: всё, кроме `declined`/`rescheduled` (уже не актуальны)."""
        new_start = scheduled_at
        new_end = scheduled_at + timedelta(minutes=duration_minutes)
        # Грубая граница по дате — не полный скан таблицы (перф), сама
        # проверка пересечения — точная, ниже.
        window_start = new_start - timedelta(days=1)
        window_end = new_end + timedelta(days=1)

        stmt = select(LessonOccurrence).where(
            LessonOccurrence.status.notin_(["declined", "rescheduled"]),
            LessonOccurrence.scheduled_at >= window_start,
            LessonOccurrence.scheduled_at <= window_end,
            (LessonOccurrence.teacher_id == teacher_id)
            | (LessonOccurrence.student_id == student_id),
        )
        if exclude_occurrence_id is not None:
            stmt = stmt.where(LessonOccurrence.id != exclude_occurrence_id)
        res = await db.execute(stmt)
        existing = res.scalars().all()

        for row in existing:
            row_start = row.scheduled_at
            row_end = row_start + timedelta(minutes=row.duration_minutes)
            if new_start < row_end and row_start < new_end:
                return True
        return False
