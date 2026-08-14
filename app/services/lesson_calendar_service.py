"""
Сервис admin-CRUD Календаря LMS (tsk-428/435): часы работы школы + групповые
слоты + их участники.

Бизнес-валидация (существование пользователей, ролей, пересечения слотов) —
здесь; прямой доступ к БД — в `app.repos.lesson_calendar_repository`.
Модель и границы — docs/specs/2026-07-26-plan-kalendar-lms.md (Фаза 1) +
tsk-435 (rework на группы после встречи с реальными данными импорта).
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_teacher import LessonOccurrenceTeacher
from app.models.lesson_slot import LessonSlot
from app.models.lesson_slot_student import LessonSlotStudent
from app.models.lesson_slot_teacher import LessonSlotTeacher
from app.models.operating_hours import OperatingHours
from app.repos.lesson_calendar_repository import (
    LessonOccurrenceParticipantRepository,
    LessonOccurrenceRepository,
    LessonOccurrenceTeacherRepository,
    LessonSlotRepository,
    LessonSlotStudentRepository,
    LessonSlotTeacherRepository,
    OperatingHoursRepository,
)
from app.services import roles_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

_operating_hours_repo = OperatingHoursRepository()
_lesson_slot_repo = LessonSlotRepository()
_slot_student_repo = LessonSlotStudentRepository()
_slot_teacher_repo = LessonSlotTeacherRepository()
_occurrence_repo = LessonOccurrenceRepository()
_participant_repo = LessonOccurrenceParticipantRepository()
_occurrence_teacher_repo = LessonOccurrenceTeacherRepository()


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


async def get_operating_hours_row(db: AsyncSession, row_id: int) -> OperatingHours:
    row = await _operating_hours_repo.get_by_id(db, row_id)
    if row is None:
        raise DomainError(f"Запись часов работы id={row_id} не найдена", status_code=404)
    return row


async def _check_operating_hours_overlap(
    db: AsyncSession, *, weekday: int, start_time: time, end_time: time, exclude_id: Optional[int] = None
) -> None:
    """Несколько окон на день недели — норма (нужно, чтобы вырезать перерыв
    внутри дня, см. tsk-436/437), но окна не должны пересекаться друг с другом."""
    existing = await _operating_hours_repo.list_for_weekday(db, weekday, exclude_id=exclude_id)
    for row in existing:
        if _operating_hours_repo.windows_overlap(start_time, end_time, row.start_time, row.end_time):
            raise DomainError(
                f"Окно {start_time}-{end_time} пересекается с уже существующим "
                f"{row.start_time}-{row.end_time} (id={row.id}) в этот день недели",
                status_code=409,
            )


async def create_operating_hours(
    db: AsyncSession,
    *,
    weekday: int,
    start_time: time,
    end_time: time,
    timezone: str,
) -> OperatingHours:
    """Добавить окно часов работы на день недели. Несколько окон на один
    weekday допустимы (напр. утро + вечер с перерывом посередине) — единственное
    ограничение — окна одного дня не должны пересекаться (см. tsk-436/437,
    операторский перерыв на личную работу разрывает окно среды пополам)."""
    await _check_operating_hours_overlap(db, weekday=weekday, start_time=start_time, end_time=end_time)
    row = await _operating_hours_repo.create(
        db, weekday=weekday, start_time=start_time, end_time=end_time, timezone=timezone,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def update_operating_hours(
    db: AsyncSession,
    row_id: int,
    *,
    start_time: Optional[time] = None,
    end_time: Optional[time] = None,
    timezone: Optional[str] = None,
) -> OperatingHours:
    row = await get_operating_hours_row(db, row_id)

    new_start = start_time if start_time is not None else row.start_time
    new_end = end_time if end_time is not None else row.end_time
    if new_end <= new_start:
        raise DomainError("end_time должен быть позже start_time", status_code=422)

    if start_time is not None or end_time is not None:
        await _check_operating_hours_overlap(
            db, weekday=row.weekday, start_time=new_start, end_time=new_end, exclude_id=row.id,
        )

    row.start_time = new_start
    row.end_time = new_end
    if timezone is not None:
        row.timezone = timezone
    await db.commit()
    await db.refresh(row)
    return row


async def delete_operating_hours(db: AsyncSession, row_id: int) -> None:
    row = await get_operating_hours_row(db, row_id)
    await _operating_hours_repo.delete(db, row)
    await db.commit()


# ─── Lesson Slot (групповой, tsk-435) ───────────────────────────────────────


async def create_lesson_slot(
    db: AsyncSession,
    *,
    teacher_id: int,
    weekday: int,
    start_time: time,
    duration_minutes: int,
    timezone: str,
    created_by: Optional[int],
    student_ids: Optional[list[int]] = None,
) -> LessonSlot:
    """Создать групповой слот преподавателя, опционально сразу с участниками
    (удобно для разового импорта расписания)."""
    await ensure_user_has_role(db, teacher_id, "teacher")

    overlap = await _lesson_slot_repo.has_overlap(
        db,
        teacher_id=teacher_id,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
    )
    if overlap:
        raise DomainError(
            "Слот пересекается по времени с другим активным слотом "
            "этого преподавателя в этот день недели",
            status_code=409,
        )

    for student_id in student_ids or []:
        await ensure_user_has_role(db, student_id, "student")

    row = await _lesson_slot_repo.create(
        db,
        teacher_id=teacher_id,
        weekday=weekday,
        start_time=start_time,
        duration_minutes=duration_minutes,
        timezone=timezone,
        created_by=created_by,
    )
    await db.flush()

    # tsk-443: teacher_id остаётся "создателем" на самой строке lesson_slot,
    # но реальный источник истины "кто ведёт" — lesson_slot_teacher; строка
    # для основного преподавателя добавляется сразу, чтобы has_overlap/
    # list_active/list_for_teacher видели его без специального случая.
    await _slot_teacher_repo.create(
        db, slot_id=row.id, teacher_id=teacher_id, added_by=created_by,
    )

    for student_id in student_ids or []:
        await _slot_student_repo.create(
            db, slot_id=row.id, student_id=student_id, added_by=created_by,
        )

    await db.commit()
    # tsk-301: слот можно создать СРАЗУ с учениками, и это тоже «появилось
    # занятие». Без пересчёта такой ученик оставался бы на demo и невидимым для
    # денег — ровно случай Грабовского, только другим путём (найдено сторожем
    # `test_tsk301_schedule_hook_guard`, а не жалобой).
    await _recalculate_money_for(db, *(student_ids or []))
    await db.refresh(row)
    logger.info(
        "lesson_slot создан: id=%s teacher=%s weekday=%s start=%s participants=%s",
        row.id, teacher_id, weekday, start_time, len(student_ids or []),
    )
    return row


async def list_lesson_slots(
    db: AsyncSession,
    *,
    teacher_id: Optional[int] = None,
) -> list[LessonSlot]:
    return await _lesson_slot_repo.list_active(db, teacher_id=teacher_id)


async def get_lesson_slot(db: AsyncSession, slot_id: int) -> LessonSlot:
    row = await _lesson_slot_repo.get_by_id(db, slot_id)
    if row is None:
        raise DomainError(f"Слот id={slot_id} не найден", status_code=404)
    return row


async def list_slot_participants(db: AsyncSession, slot_id: int) -> list[LessonSlotStudent]:
    await get_lesson_slot(db, slot_id)
    return await _slot_student_repo.list_for_slot(db, slot_id)


async def list_slot_teachers(db: AsyncSession, slot_id: int) -> list[LessonSlotTeacher]:
    await get_lesson_slot(db, slot_id)
    return await _slot_teacher_repo.list_for_slot(db, slot_id)


async def update_lesson_slot(
    db: AsyncSession,
    slot_id: int,
    *,
    weekday: Optional[int] = None,
    start_time: Optional[time] = None,
    duration_minutes: Optional[int] = None,
    timezone: Optional[str] = None,
    is_active: Optional[bool] = None,
    teacher_id: Optional[int] = None,
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
            weekday=new_weekday,
            start_time=new_start_time,
            duration_minutes=new_duration,
            exclude_slot_id=row.id,
        )
        if overlap:
            raise DomainError(
                "Новое время слота пересекается с другим активным слотом",
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

    # tsk-437: смена основного преподавателя слота.
    #
    # Раньше поля не было в контракте вовсе — сменить ведущего можно было
    # только пересозданием слота, а с ним терялись прикреплённые ученики.
    #
    # Будущие занятия переводим на нового: они ещё не состоялись, и вести их
    # будет он. Прошедшие НЕ трогаем — это история, кто вёл, тот и вёл.
    if teacher_id is not None and teacher_id != row.teacher_id:
        await ensure_user_has_role(db, teacher_id, "teacher")
        old_teacher_id = row.teacher_id
        row.teacher_id = teacher_id
        await db.flush()

        await db.execute(
            text(
                "UPDATE lesson_occurrence SET teacher_id = :new "
                "WHERE slot_id = :slot_id AND scheduled_at > now()"
            ),
            {"new": teacher_id, "slot_id": slot_id},
        )
        # Состав ведущих у будущих занятий: снимаем прежнего основного, если он
        # не остался в составе слота отдельной записью, и ставим нового.
        keeps_leading = any(
            t.teacher_id == old_teacher_id for t in await list_slot_teachers(db, slot_id)
        )
        if not keeps_leading:
            await db.execute(
                text(
                    """
                    DELETE FROM lesson_occurrence_teacher lot USING lesson_occurrence o
                    WHERE lot.occurrence_id = o.id AND lot.teacher_id = :old
                      AND o.slot_id = :slot_id AND o.scheduled_at > now()
                      -- tsk-492: разовые назначения не трогаем. Их поставили
                      -- на КОНКРЕТНОЕ занятие отдельным решением, и смена
                      -- основного по слоту к ним отношения не имеет.
                      AND NOT lot.is_one_off
                    """
                ),
                {"old": old_teacher_id, "slot_id": slot_id},
            )
        await db.execute(
            text(
                """
                INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id)
                SELECT o.id, :new FROM lesson_occurrence o
                WHERE o.slot_id = :slot_id AND o.scheduled_at > now()
                ON CONFLICT DO NOTHING
                """
            ),
            {"new": teacher_id, "slot_id": slot_id},
        )

    await db.commit()
    await db.refresh(row)
    return row


async def deactivate_lesson_slot(db: AsyncSession, slot_id: int) -> None:
    """Деактивация вместо удаления — сохраняет историю occurrence (см. спек)."""
    row = await get_lesson_slot(db, slot_id)
    row.is_active = False
    await db.commit()


async def _attach_student_to_slot(
    db: AsyncSession, slot_id: int, student_id: int, *, added_by: Optional[int]
) -> LessonSlotStudent:
    """Связать ученика со слотом и добавить его во ВСЕ уже сгенерированные
    БУДУЩИЕ occurrence этого слота — иначе он не увидит существующие занятия
    до следующего тика генератора.

    Без commit: вызывающий решает границы транзакции (перевод между слотами
    обязан быть одним целым)."""
    await ensure_user_has_role(db, student_id, "student")

    existing = await _slot_student_repo.get(db, slot_id=slot_id, student_id=student_id)
    if existing is not None and existing.is_active:
        return existing

    if existing is not None:
        existing.is_active = True
        row = existing
    else:
        row = await _slot_student_repo.create(
            db, slot_id=slot_id, student_id=student_id, added_by=added_by,
        )
    await db.flush()

    future_occurrences = await _occurrence_repo.list_for_slot(
        db, slot_id, from_dt=datetime.now(timezone.utc),
    )
    for occurrence in future_occurrences:
        already = await _participant_repo.get(
            db, occurrence_id=occurrence.id, student_id=student_id
        )
        if already is None:
            await _participant_repo.create(
                db, occurrence_id=occurrence.id, student_id=student_id, status="scheduled",
            )
    return row


async def _detach_student_from_slot(
    db: AsyncSession, slot_id: int, student_id: int
) -> LessonSlotStudent:
    """Снять ученика со слота и убрать его из БУДУЩИХ occurrence этого слота,
    где он ещё ничего не решил сам (`status='scheduled'`).

    Записи с собственным действием ученика (`confirmed`, `declined`, `no_show`,
    `rescheduled`) НЕ трогаем: это его история и уже отмеченная явка.

    tsk-491: раньше чистки не было вовсе — гасилась только связь со слотом,
    а ученик продолжал числиться в уже созданных занятиях (на проде такой
    «хвост» и нашёлся: ученик 4526, слот 4, 2 будущих занятия). Открепление
    выглядело сработавшим, но преподаватель видел ученика в списке явки.

    Без commit — см. `_attach_student_to_slot`."""
    row = await _slot_student_repo.get(db, slot_id=slot_id, student_id=student_id)
    if row is None or not row.is_active:
        raise DomainError(
            f"Ученик id={student_id} не числится активным участником слота id={slot_id}",
            status_code=404,
        )
    row.is_active = False

    future_occurrences = await _occurrence_repo.list_for_slot(
        db, slot_id, from_dt=datetime.now(timezone.utc),
    )
    for occurrence in future_occurrences:
        participant = await _participant_repo.get(
            db, occurrence_id=occurrence.id, student_id=student_id
        )
        if participant is not None and participant.status == "scheduled":
            await db.delete(participant)
    await db.flush()
    return row


async def _recalculate_money_for(db: AsyncSession, *student_ids: int) -> None:
    """Пересчитать открытые начисления учеников после смены расписания.

    tsk-548: сумма месяца считается по ПОСТОЯННОМУ расписанию, но пересчёт
    никто не звал при его изменении — только при смене тарифа, ручной цены и
    перерыва. На проде это дало три завышенных счёта: ученик перешёл с двух
    занятий в неделю на одно, а сумма осталась прежней. Деньги, названные
    человеку неверно, дороже лишнего запроса.

    Импорт локальный: `charge_service` тянет за собой прайс, а расписание
    грузится раньше него.
    """
    from app.services import charge_service, subscription_service

    for student_id in dict.fromkeys(student_ids):
        # tsk-301: появление занятий и есть признак того, что человек стал
        # учеником по-настоящему — переводим с demo на base. Повышение идёт
        # ТОЛЬКО с demo (см. `UPGRADABLE_FROM`): на base_legacy держится старая
        # цена 37 действующих учеников, и правило без этой оговорки поднимало бы
        # её каждому, кому меняют расписание.
        #
        # Строго ДО пересчёта: иначе месяц посчитается по прежней группе, а новая
        # применится только со следующего изменения расписания.
        try:
            await subscription_service.upgrade_on_schedule(db, student_id)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "tsk-301: автоперевод на base не удался, ученик %s", student_id
            )
        await charge_service.recalculate_open_months_for_student(db, student_id=student_id)


async def add_slot_participant(
    db: AsyncSession, slot_id: int, student_id: int, *, added_by: Optional[int]
) -> LessonSlotStudent:
    """Добавить ученика в групповой слот (с бэкфиллом будущих занятий)."""
    await get_lesson_slot(db, slot_id)
    row = await _attach_student_to_slot(db, slot_id, student_id, added_by=added_by)
    await db.commit()
    await _recalculate_money_for(db, student_id)
    await db.refresh(row)
    return row


async def remove_slot_participant(db: AsyncSession, slot_id: int, student_id: int) -> None:
    """Убрать ученика из слота (мягко) вместе с будущими занятиями."""
    await get_lesson_slot(db, slot_id)
    await _detach_student_from_slot(db, slot_id, student_id)
    await db.commit()
    await _recalculate_money_for(db, student_id)


async def transfer_slot_participant(
    db: AsyncSession,
    *,
    source_slot_id: int,
    target_slot_id: int,
    student_id: int,
    added_by: Optional[int],
) -> LessonSlotStudent:
    """Перевести ученика из одного слота в другой НАСОВСЕМ, одним действием.

    Открепление и прикрепление по отдельности дают тот же результат только
    если не забыть второй шаг и если первый не упадёт на полпути. Здесь оба
    шага — одна транзакция: ученик либо переехал целиком, либо остался там,
    где был.

    Прошедшие занятия и уже отмеченная явка не трогаются — это история.
    """
    if source_slot_id == target_slot_id:
        raise DomainError(
            "Исходный и целевой слоты совпадают — переводить некуда",
            status_code=422,
        )

    await get_lesson_slot(db, source_slot_id)
    target = await get_lesson_slot(db, target_slot_id)
    if not target.is_active:
        raise DomainError(
            f"Слот id={target_slot_id} выключен — ученик остался бы без занятий",
            status_code=409,
        )

    # Ученик не может быть в двух местах в одно время. Проверяем ДО правок,
    # чтобы не оставить его в промежуточном состоянии.
    conflict = await _find_conflicting_slot(
        db,
        student_id=student_id,
        target=target,
        exclude_slot_ids={source_slot_id, target_slot_id},
    )
    if conflict is not None:
        raise DomainError(
            f"У ученика уже есть слот id={conflict.id} в это же время "
            f"({WEEKDAY_NAMES[conflict.weekday]}, {conflict.start_time:%H:%M}) — "
            "сначала освободите его",
            status_code=409,
        )

    await _detach_student_from_slot(db, source_slot_id, student_id)
    row = await _attach_student_to_slot(
        db, target_slot_id, student_id, added_by=added_by
    )
    await db.commit()
    # tsk-301/tsk-548: перевод между слотами меняет и частоту занятий, и сам факт
    # их наличия. Без пересчёта сумма осталась бы от прежнего слота, а тариф — от
    # состояния «занятий нет».
    await _recalculate_money_for(db, student_id)
    await db.refresh(row)
    return row


WEEKDAY_NAMES = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


async def _find_conflicting_slot(
    db: AsyncSession,
    *,
    student_id: int,
    target: LessonSlot,
    exclude_slot_ids: set[int],
) -> Optional[LessonSlot]:
    """Активный слот ученика, который пересекается по времени с целевым.

    Сравниваем в пределах дня недели: слот — повторяющееся окно, поэтому
    достаточно пересечения отрезков [начало, начало+длительность).
    """
    memberships = await _slot_student_repo.list_for_student(db, student_id)
    target_start = _minutes(target.start_time)
    target_end = target_start + target.duration_minutes

    for membership in memberships:
        if membership.slot_id in exclude_slot_ids:
            continue
        other = await _lesson_slot_repo.get_by_id(db, membership.slot_id)
        if other is None or not other.is_active or other.weekday != target.weekday:
            continue
        other_start = _minutes(other.start_time)
        if other_start < target_end and target_start < other_start + other.duration_minutes:
            return other
    return None


def _minutes(value: time) -> int:
    """Время начала в минутах от полуночи — для сравнения отрезков."""
    return value.hour * 60 + value.minute


async def add_slot_teacher(
    db: AsyncSession, slot_id: int, teacher_id: int, *, added_by: Optional[int]
) -> LessonSlotTeacher:
    """Добавить со-преподавателя в групповой слот (совместное ведение,
    tsk-443) — ученики этого слота становятся видны и ему тоже, явка общая
    (один occurrence, один список участников). Бэкфиллит его во ВСЕ уже
    сгенерированные БУДУЩИЕ occurrence этого слота — иначе он не увидит уже
    существующие занятия до следующего тика генератора (тот же паттерн, что
    `add_slot_participant`)."""
    await get_lesson_slot(db, slot_id)
    await ensure_user_has_role(db, teacher_id, "teacher")

    existing = await _slot_teacher_repo.get(db, slot_id=slot_id, teacher_id=teacher_id)
    if existing is not None and existing.is_active:
        return existing

    if existing is not None:
        existing.is_active = True
        row = existing
    else:
        row = await _slot_teacher_repo.create(
            db, slot_id=slot_id, teacher_id=teacher_id, added_by=added_by,
        )
    await db.flush()

    future_occurrences = await _occurrence_repo.list_for_slot(
        db, slot_id, from_dt=datetime.now(timezone.utc),
    )
    for occurrence in future_occurrences:
        already = await _occurrence_teacher_repo.get(
            db, occurrence_id=occurrence.id, teacher_id=teacher_id
        )
        if already is None:
            await _occurrence_teacher_repo.create(
                db, occurrence_id=occurrence.id, teacher_id=teacher_id,
            )

    await db.commit()
    await db.refresh(row)
    logger.info(
        "lesson_slot_teacher добавлен: slot=%s teacher=%s backfilled_occurrences=%s",
        slot_id, teacher_id, len(future_occurrences),
    )
    return row


async def remove_slot_teacher(db: AsyncSession, slot_id: int, teacher_id: int) -> None:
    """Убрать со-преподавателя из слота (мягко).

    tsk-437: последнего снять НЕЛЬЗЯ (409). Пустой состав генератор занятий
    молча трактует как «ведёт основной» (`slot_teacher_ids or [slot.teacher_id]`
    в `lesson_occurrence_generator_service`), то есть снятие выглядело бы
    успешным, а по факту вернуло бы прежнего ведущего. Раньше эта функция
    наружу не выставлялась и вызвать её было неоткуда; с вебом методиста —
    можно, поэтому защита нужна.
    """
    await get_lesson_slot(db, slot_id)
    row = await _slot_teacher_repo.get(db, slot_id=slot_id, teacher_id=teacher_id)
    if row is None or not row.is_active:
        raise DomainError(
            f"Преподаватель id={teacher_id} не числится активным на слоте id={slot_id}",
            status_code=404,
        )

    active = await list_slot_teachers(db, slot_id)
    if len(active) <= 1:
        raise DomainError(
            "Нельзя снять последнего преподавателя слота — занятия останутся без "
            "ведущего. Сначала поставьте другого.",
            status_code=409,
        )

    row.is_active = False

    # tsk-492: снятие со слота действует ПОСТОЯННО — значит убирает его и из
    # уже созданных будущих занятий. Иначе связь погашена, а преподаватель две
    # недели продолжает видеть занятия и получать письма о пропусках: ровно тот
    # дефект, что чинили для ученика в tsk-491, только с другой стороны.
    # Разовые назначения не трогаем — это отдельные решения по конкретным
    # занятиям, а не следствие состава слота.
    await db.execute(
        text(
            """
            DELETE FROM lesson_occurrence_teacher lot USING lesson_occurrence o
            WHERE lot.occurrence_id = o.id AND lot.teacher_id = :tid
              AND o.slot_id = :slot_id AND o.scheduled_at > now()
              AND NOT lot.is_one_off
            """
        ),
        {"tid": teacher_id, "slot_id": slot_id},
    )
    await db.commit()


async def list_occurrence_teachers(
    db: AsyncSession, occurrence_id: int
) -> list[LessonOccurrenceTeacher]:
    """Кто ведёт ЭТО занятие (с учётом разовых исключений)."""
    await _get_occurrence_or_404(db, occurrence_id)
    return await _occurrence_teacher_repo.list_for_occurrence(db, occurrence_id)


async def add_occurrence_teacher(
    db: AsyncSession, occurrence_id: int, teacher_id: int
) -> LessonOccurrenceTeacher:
    """Поставить преподавателя на ОДНО занятие, не трогая состав слота.

    Разовое усиление или подмена: «в этот четверг ведёт Светлана». Постоянный
    состав остаётся прежним, следующие занятия пойдут как обычно.
    """
    await _get_occurrence_or_404(db, occurrence_id)
    await ensure_user_has_role(db, teacher_id, "teacher")

    row = await _occurrence_teacher_repo.get(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    if row is None:
        row = await _occurrence_teacher_repo.create(
            db, occurrence_id=occurrence_id, teacher_id=teacher_id, is_one_off=True,
        )
    else:
        # Был погашен (снят с этого занятия) — возвращаем. Пометку «разовый»
        # не снимаем и не ставим: она говорит, откуда строка взялась.
        row.is_active = True
    await db.commit()
    await db.refresh(row)
    return row


async def remove_occurrence_teacher(
    db: AsyncSession, occurrence_id: int, teacher_id: int
) -> None:
    """Снять преподавателя с ОДНОГО занятия, не трогая состав слота.

    Разовая строка удаляется. Строка из состава слота ГАСИТСЯ, а не удаляется:
    генератор занятий досыпает состав слота каждый тик через
    `ON CONFLICT DO NOTHING` — удалённую строку он вернул бы на следующем тике,
    и снятие продержалось бы до вечера. Погашенную не трогает.

    Последнего ведущего снять нельзя (409) — занятие осталось бы без никого.
    """
    occurrence = await _get_occurrence_or_404(db, occurrence_id)
    row = await _occurrence_teacher_repo.get(
        db, occurrence_id=occurrence_id, teacher_id=teacher_id
    )
    leading_now = {t.teacher_id for t in
                   await _occurrence_teacher_repo.list_for_occurrence(db, occurrence_id)}
    if not leading_now:
        # Строк ещё нет — занятие ведёт основной по колонке (генератор не
        # дошёл либо занятие ad-hoc). Тогда снять можно только его.
        leading_now = {occurrence.teacher_id}

    if teacher_id not in leading_now:
        raise DomainError(
            f"Преподаватель id={teacher_id} не ведёт занятие id={occurrence_id}",
            status_code=404,
        )
    if len(leading_now) <= 1:
        raise DomainError(
            "Нельзя снять последнего преподавателя занятия — оно осталось бы без "
            "ведущего. Сначала поставьте другого.",
            status_code=409,
        )

    if row is None:
        # Основной по колонке, строки нет — заводим сразу погашенной, иначе
        # гасить нечего, а удалить колонку у занятия мы не можем.
        await _occurrence_teacher_repo.create(
            db, occurrence_id=occurrence_id, teacher_id=teacher_id, is_active=False,
        )
    elif row.is_one_off:
        await db.delete(row)
    else:
        row.is_active = False
    await db.commit()


async def _get_occurrence_or_404(db: AsyncSession, occurrence_id: int) -> LessonOccurrence:
    occurrence = await _occurrence_repo.get_by_id(db, occurrence_id)
    if occurrence is None:
        raise DomainError(f"Занятие id={occurrence_id} не найдено", status_code=404)
    return occurrence


def _time_in_window(local_time: time, start_time: time, duration_minutes: int) -> bool:
    """`local_time` попадает в [start_time, start_time+duration)? Полночь
    не пересекается (занятия внутри одних суток, тот же допуск, что и
    `is_within_operating_hours`)."""
    dummy_date = datetime(2000, 1, 1)
    start_dt = datetime.combine(dummy_date, start_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    if end_dt.date() != start_dt.date():
        return False
    candidate_dt = datetime.combine(dummy_date, local_time)
    return start_dt <= candidate_dt < end_dt


async def list_teachers_for_time(
    db: AsyncSession, *, scheduled_at: datetime, duration_minutes: int = 60,
) -> list["Users"]:
    """Преподаватели, у которых уже есть закреплённый слот на это конкретное
    время (tsk-443, реальный кейс: Денис Ильин записывался на Пн 17:00,
    система предложила выбрать из 4 привязанных преподавателей, хотя слот
    там ровно один — оператор).

    Возвращает ОДНОГО представителя НА КАЖДЫЙ отдельный слот, покрывающий
    время (`slot.teacher_id`, основной), а не по преподавателю — выбор между
    со-преподавателями ОДНОГО И ТОГО ЖЕ слота бессмыслен (это одно занятие,
    tsk-443), даже если их несколько. Выбор нужен только если время
    покрывают ДВА РАЗНЫХ независимых слота (разных, не пересекающихся по
    преподавателям — `has_overlap` это гарантирует).

    Пустой список — ни один активный слот школы не покрывает это время
    (истинный ad-hoc вне расписания) — вызывающий код должен упасть на
    дефолт (полный список привязанных преподавателей ученика).
    """
    tz = ZoneInfo("Europe/Moscow")
    local_dt = scheduled_at.astimezone(tz)
    weekday = local_dt.weekday()
    local_time = local_dt.time()

    all_active_slots = await _lesson_slot_repo.list_active(db)
    matching_slots = [
        s for s in all_active_slots
        if s.weekday == weekday and _time_in_window(local_time, s.start_time, s.duration_minutes)
    ]
    if not matching_slots:
        return []

    representative_teacher_ids = {s.teacher_id for s in matching_slots}

    from app.models.users import Users  # noqa: PLC0415 — избегаем circular import
    from sqlalchemy import select as _select  # noqa: PLC0415

    res = await db.execute(_select(Users).where(Users.id.in_(representative_teacher_ids)))
    return list(res.scalars().all())


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
