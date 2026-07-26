"""
Репозитории Календаря LMS (tsk-428/429/430/435): operating_hours, lesson_slot
(+ участники), lesson_occurrence (+ участники).

Прямой доступ к БД, без бизнес-валидации — она в сервисах
(`lesson_calendar_service`, `lesson_attendance_service`,
`lesson_occurrence_service`).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_student import LessonSlotStudent
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
    """CRUD закреплённых групповых слотов преподавателя (участники — отдельно,
    см. `LessonSlotStudentRepository`)."""

    async def get_by_id(self, db: AsyncSession, slot_id: int) -> Optional[LessonSlot]:
        return await db.get(LessonSlot, slot_id)

    async def list_active(
        self,
        db: AsyncSession,
        *,
        teacher_id: Optional[int] = None,
    ) -> list[LessonSlot]:
        stmt = select(LessonSlot).where(LessonSlot.is_active.is_(True))
        if teacher_id is not None:
            stmt = stmt.where(LessonSlot.teacher_id == teacher_id)
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
        weekday: int,
        start_time,
        duration_minutes: int,
        exclude_slot_id: Optional[int] = None,
    ) -> bool:
        """Есть ли у преподавателя другой активный слот (другое групповое
        занятие), пересекающийся по времени в этот день недели — teacher-only,
        участники слота больше не участвуют в этой проверке (группа —
        by design несколько учеников на одно время одного преподавателя)."""
        stmt = select(LessonSlot).where(
            LessonSlot.is_active.is_(True),
            LessonSlot.weekday == weekday,
            LessonSlot.teacher_id == teacher_id,
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


class LessonSlotStudentRepository:
    """Участники закреплённого группового слота (M2M)."""

    async def get(
        self, db: AsyncSession, *, slot_id: int, student_id: int
    ) -> Optional[LessonSlotStudent]:
        res = await db.execute(
            select(LessonSlotStudent).where(
                LessonSlotStudent.slot_id == slot_id,
                LessonSlotStudent.student_id == student_id,
            )
        )
        return res.scalar_one_or_none()

    async def list_for_slot(
        self, db: AsyncSession, slot_id: int, *, active_only: bool = True
    ) -> list[LessonSlotStudent]:
        stmt = select(LessonSlotStudent).where(LessonSlotStudent.slot_id == slot_id)
        if active_only:
            stmt = stmt.where(LessonSlotStudent.is_active.is_(True))
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def list_for_slots(
        self, db: AsyncSession, slot_ids: list[int], *, active_only: bool = True
    ) -> list[LessonSlotStudent]:
        if not slot_ids:
            return []
        stmt = select(LessonSlotStudent).where(LessonSlotStudent.slot_id.in_(slot_ids))
        if active_only:
            stmt = stmt.where(LessonSlotStudent.is_active.is_(True))
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def list_for_student(
        self, db: AsyncSession, student_id: int, *, active_only: bool = True
    ) -> list[LessonSlotStudent]:
        stmt = select(LessonSlotStudent).where(LessonSlotStudent.student_id == student_id)
        if active_only:
            stmt = stmt.where(LessonSlotStudent.is_active.is_(True))
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> LessonSlotStudent:
        row = LessonSlotStudent(**fields)
        db.add(row)
        await db.flush()
        return row


class LessonOccurrenceRepository:
    """Занятия (create — генератор/ad-hoc сервис). Участники — отдельно,
    см. `LessonOccurrenceParticipantRepository`."""

    async def get_by_id(self, db: AsyncSession, occurrence_id: int) -> Optional[LessonOccurrence]:
        return await db.get(LessonOccurrence, occurrence_id)

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


class LessonOccurrenceParticipantRepository:
    """Явка ПО КАЖДОМУ участнику occurrence независимо."""

    async def get(
        self, db: AsyncSession, *, occurrence_id: int, student_id: int
    ) -> Optional[LessonOccurrenceParticipant]:
        res = await db.execute(
            select(LessonOccurrenceParticipant).where(
                LessonOccurrenceParticipant.occurrence_id == occurrence_id,
                LessonOccurrenceParticipant.student_id == student_id,
            )
        )
        return res.scalar_one_or_none()

    async def get_by_id(
        self, db: AsyncSession, participant_id: int
    ) -> Optional[LessonOccurrenceParticipant]:
        return await db.get(LessonOccurrenceParticipant, participant_id)

    async def list_for_occurrence(
        self, db: AsyncSession, occurrence_id: int
    ) -> list[LessonOccurrenceParticipant]:
        res = await db.execute(
            select(LessonOccurrenceParticipant).where(
                LessonOccurrenceParticipant.occurrence_id == occurrence_id
            )
        )
        return list(res.scalars().all())

    async def list_for_occurrences(
        self, db: AsyncSession, occurrence_ids: list[int]
    ) -> list[LessonOccurrenceParticipant]:
        if not occurrence_ids:
            return []
        res = await db.execute(
            select(LessonOccurrenceParticipant).where(
                LessonOccurrenceParticipant.occurrence_id.in_(occurrence_ids)
            )
        )
        return list(res.scalars().all())

    async def list_for_student(
        self,
        db: AsyncSession,
        *,
        student_id: int,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: int = 50,
    ) -> list[tuple[LessonOccurrenceParticipant, LessonOccurrence]]:
        """Occurrence-ы ученика (через участие) + сам occurrence, отсортировано
        по времени. Возвращает пары (participant, occurrence) — участник несёт
        статус явки, occurrence — время/слот/преподаватель."""
        stmt = (
            select(LessonOccurrenceParticipant, LessonOccurrence)
            .join(LessonOccurrence, LessonOccurrence.id == LessonOccurrenceParticipant.occurrence_id)
            .where(LessonOccurrenceParticipant.student_id == student_id)
        )
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at <= to_dt)
        stmt = stmt.order_by(LessonOccurrence.scheduled_at.asc()).limit(limit)
        res = await db.execute(stmt)
        return [(p, o) for p, o in res.all()]

    async def create(self, db: AsyncSession, **fields) -> LessonOccurrenceParticipant:
        row = LessonOccurrenceParticipant(**fields)
        db.add(row)
        await db.flush()
        return row

    async def has_student_overlap(
        self,
        db: AsyncSession,
        *,
        student_id: int,
        scheduled_at: datetime,
        duration_minutes: int,
        exclude_occurrence_id: Optional[int] = None,
    ) -> bool:
        """Пересечение по РЕАЛЬНОМУ диапазону времени с любым другим активным
        участием этого ученика (не teacher — преподаватель по design может
        вести несколько occurrence одновременно, это и есть группа).
        Незакрытые статусы участника: всё, кроме `declined`/`rescheduled`."""
        new_start = scheduled_at
        new_end = scheduled_at + timedelta(minutes=duration_minutes)
        window_start = new_start - timedelta(days=1)
        window_end = new_end + timedelta(days=1)

        stmt = (
            select(LessonOccurrenceParticipant, LessonOccurrence)
            .join(LessonOccurrence, LessonOccurrence.id == LessonOccurrenceParticipant.occurrence_id)
            .where(
                LessonOccurrenceParticipant.student_id == student_id,
                LessonOccurrenceParticipant.status.notin_(["declined", "rescheduled"]),
                LessonOccurrence.scheduled_at >= window_start,
                LessonOccurrence.scheduled_at <= window_end,
            )
        )
        if exclude_occurrence_id is not None:
            stmt = stmt.where(LessonOccurrence.id != exclude_occurrence_id)
        res = await db.execute(stmt)
        existing = res.all()

        for _participant, occurrence in existing:
            row_start = occurrence.scheduled_at
            row_end = row_start + timedelta(minutes=occurrence.duration_minutes)
            if new_start < row_end and row_start < new_end:
                return True
        return False
