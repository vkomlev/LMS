"""
Репозитории Календаря LMS Фаза 1 (tsk-428): operating_hours + lesson_slot.

Прямой доступ к БД, без бизнес-валидации — она в `lesson_calendar_service`.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
