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

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.lesson_occurrence_teacher import LessonOccurrenceTeacher
from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_student import LessonSlotStudent
from app.models.lesson_slot_teacher import LessonSlotTeacher
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

    async def list_for_weekday(
        self, db: AsyncSession, weekday: int, *, exclude_id: Optional[int] = None
    ) -> list[OperatingHours]:
        stmt = select(OperatingHours).where(OperatingHours.weekday == weekday)
        if exclude_id is not None:
            stmt = stmt.where(OperatingHours.id != exclude_id)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> OperatingHours:
        row = OperatingHours(**fields)
        db.add(row)
        await db.flush()
        return row

    async def delete(self, db: AsyncSession, row: OperatingHours) -> None:
        await db.delete(row)

    @staticmethod
    def windows_overlap(a_start, a_end, b_start, b_end) -> bool:
        """Пересекаются ли два окна [start, end) в рамках одного дня недели."""
        return a_start < b_end and b_start < a_end


class LessonSlotRepository:
    """CRUD закреплённых групповых слотов преподавателя (участники — отдельно,
    см. `LessonSlotStudentRepository`; со-преподаватели — `LessonSlotTeacherRepository`).

    `list_active(teacher_id=...)` и `has_overlap` фильтруют через M2M
    `lesson_slot_teacher` (не через `LessonSlot.teacher_id` напрямую) —
    источник истины "кто ведёт слот" после tsk-443 (совместное ведение).
    `LessonSlot.teacher_id` остаётся "создателем" для аудита. Фильтр по
    преподавателю матчит `teacher_id == X` ИЛИ активную строку в M2M
    `lesson_slot_teacher` (не эксклюзивно M2M) — так слоты, заведённые в
    обход `create_lesson_slot` (напр. напрямую через ORM в старых тестах,
    до tsk-443 и backfill-миграции), продолжают находиться без изменений;
    со-преподаватели матчат через M2M.
    """

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
            stmt = stmt.where(
                or_(
                    LessonSlot.teacher_id == teacher_id,
                    LessonSlot.id.in_(
                        select(LessonSlotTeacher.slot_id).where(
                            LessonSlotTeacher.teacher_id == teacher_id,
                            LessonSlotTeacher.is_active.is_(True),
                        )
                    ),
                )
            )
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
        """Есть ли у преподавателя (как у основного ИЛИ со-преподавателя)
        другой активный слот, пересекающийся по времени в этот день недели —
        teacher-only, участники слота в этой проверке не участвуют (группа —
        by design несколько учеников на одно время одного/нескольких
        преподавателей)."""
        stmt = select(LessonSlot).where(
            LessonSlot.is_active.is_(True),
            LessonSlot.weekday == weekday,
            or_(
                LessonSlot.teacher_id == teacher_id,
                LessonSlot.id.in_(
                    select(LessonSlotTeacher.slot_id).where(
                        LessonSlotTeacher.teacher_id == teacher_id,
                        LessonSlotTeacher.is_active.is_(True),
                    )
                ),
            ),
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


class LessonSlotTeacherRepository:
    """Со-преподаватели закреплённого слота (M2M, tsk-443)."""

    async def get(
        self, db: AsyncSession, *, slot_id: int, teacher_id: int
    ) -> Optional[LessonSlotTeacher]:
        res = await db.execute(
            select(LessonSlotTeacher).where(
                LessonSlotTeacher.slot_id == slot_id,
                LessonSlotTeacher.teacher_id == teacher_id,
            )
        )
        return res.scalar_one_or_none()

    async def list_for_slot(
        self, db: AsyncSession, slot_id: int, *, active_only: bool = True
    ) -> list[LessonSlotTeacher]:
        stmt = select(LessonSlotTeacher).where(LessonSlotTeacher.slot_id == slot_id)
        if active_only:
            stmt = stmt.where(LessonSlotTeacher.is_active.is_(True))
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> LessonSlotTeacher:
        row = LessonSlotTeacher(**fields)
        db.add(row)
        await db.flush()
        return row


class LessonOccurrenceTeacherRepository:
    """Преподаватели конкретного занятия (M2M, tsk-443) — заполняется
    генератором из `lesson_slot_teacher`, источник истины для видимости
    occurrence в кабинете преподавателя."""

    async def get(
        self, db: AsyncSession, *, occurrence_id: int, teacher_id: int
    ) -> Optional[LessonOccurrenceTeacher]:
        res = await db.execute(
            select(LessonOccurrenceTeacher).where(
                LessonOccurrenceTeacher.occurrence_id == occurrence_id,
                LessonOccurrenceTeacher.teacher_id == teacher_id,
            )
        )
        return res.scalar_one_or_none()

    async def list_for_occurrence(
        self, db: AsyncSession, occurrence_id: int
    ) -> list[LessonOccurrenceTeacher]:
        res = await db.execute(
            select(LessonOccurrenceTeacher).where(
                LessonOccurrenceTeacher.occurrence_id == occurrence_id
            )
        )
        return list(res.scalars().all())

    async def create(self, db: AsyncSession, **fields) -> LessonOccurrenceTeacher:
        row = LessonOccurrenceTeacher(**fields)
        db.add(row)
        await db.flush()
        return row


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
    см. `LessonOccurrenceParticipantRepository`; со-преподаватели —
    `LessonOccurrenceTeacherRepository`."""

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
        """Занятия, где этот преподаватель — основной (`teacher_id`) ИЛИ
        со-преподаватель (через `lesson_occurrence_teacher`, tsk-443:
        совместное ведение; OR, не эксклюзивно M2M — см. docstring класса)."""
        stmt = select(LessonOccurrence).where(
            or_(
                LessonOccurrence.teacher_id == teacher_id,
                LessonOccurrence.id.in_(
                    select(LessonOccurrenceTeacher.occurrence_id).where(
                        LessonOccurrenceTeacher.teacher_id == teacher_id
                    )
                ),
            )
        )
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at <= to_dt)
        stmt = stmt.order_by(LessonOccurrence.scheduled_at.asc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def list_for_teachers(
        self,
        db: AsyncSession,
        *,
        teacher_ids: list[int],
        from_dt: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[LessonOccurrence]:
        """Как `list_for_teacher`, но для НЕСКОЛЬКИХ преподавателей сразу
        (объединение, не пересечение) — используется для подбора ближайших
        занятий, к которым ученик может присоединиться (tsk-021/443:
        `list_bookable_occurrences_for_student`)."""
        if not teacher_ids:
            return []
        stmt = select(LessonOccurrence).where(
            or_(
                LessonOccurrence.teacher_id.in_(teacher_ids),
                LessonOccurrence.id.in_(
                    select(LessonOccurrenceTeacher.occurrence_id).where(
                        LessonOccurrenceTeacher.teacher_id.in_(teacher_ids)
                    )
                ),
            )
        )
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
        stmt = stmt.order_by(LessonOccurrence.scheduled_at.asc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def list_teacher_names_for_occurrences(
        self, db: AsyncSession, occurrence_ids: list[int],
    ) -> dict[int, list[str]]:
        """Имена преподавателей по каждому occurrence (через M2M) — для
        подписи в списке "ближайшие занятия" на стороне ученика."""
        if not occurrence_ids:
            return {}
        from app.models.users import Users  # noqa: PLC0415 — избегаем circular import

        rows = (
            await db.execute(
                select(LessonOccurrenceTeacher.occurrence_id, Users.full_name)
                .join(Users, Users.id == LessonOccurrenceTeacher.teacher_id)
                .where(LessonOccurrenceTeacher.occurrence_id.in_(occurrence_ids))
            )
        ).all()
        names_by_occurrence: dict[int, list[str]] = {}
        for occurrence_id, full_name in rows:
            names_by_occurrence.setdefault(occurrence_id, []).append(full_name or "")
        return names_by_occurrence

    async def list_for_slot(
        self, db: AsyncSession, slot_id: int, *, from_dt: Optional[datetime] = None,
    ) -> list[LessonOccurrence]:
        """Все occurrence конкретного слота (независимо от того, кто из
        преподавателей уже к ним привязан) — для бэкфилла со-преподавателя
        (`add_slot_teacher`), где `list_for_teacher` ещё пуст по построению."""
        stmt = select(LessonOccurrence).where(LessonOccurrence.slot_id == slot_id)
        if from_dt is not None:
            stmt = stmt.where(LessonOccurrence.scheduled_at >= from_dt)
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
            select(LessonOccurrenceParticipant)
            .where(LessonOccurrenceParticipant.occurrence_id.in_(occurrence_ids))
            .order_by(LessonOccurrenceParticipant.id)
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

    async def get_current_scheduled_for_student(
        self, db: AsyncSession, *, student_id: int, now: datetime
    ) -> Optional[LessonOccurrenceParticipant]:
        """Активное ПРЯМО СЕЙЧАС занятие ученика — участие ещё в статусе
        `scheduled` (явку не подтверждал и не отказывался), `now` попадает в
        [scheduled_at, scheduled_at+duration). Для авто-подтверждения явки по
        реальному учебному действию (tsk-439). `status='scheduled'` в самом
        SQL — declined/rescheduled/no_show/completed/confirmed отсекаются
        сразу, без Python-фильтра."""
        stmt = (
            select(LessonOccurrenceParticipant, LessonOccurrence)
            .join(LessonOccurrence, LessonOccurrence.id == LessonOccurrenceParticipant.occurrence_id)
            .where(
                LessonOccurrenceParticipant.student_id == student_id,
                LessonOccurrenceParticipant.status == "scheduled",
                LessonOccurrence.scheduled_at <= now,
            )
        )
        res = await db.execute(stmt)
        for participant, occurrence in res.all():
            window_end = occurrence.scheduled_at + timedelta(minutes=occurrence.duration_minutes)
            if now < window_end:
                return participant
        return None
