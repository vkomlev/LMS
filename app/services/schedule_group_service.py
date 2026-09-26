"""
Группы расписания (tsk-1124): аудитория + предмет.

У слота ровно одна группа (`lesson_slot.group_id`), у ученика — одна или
несколько (`user_schedule_group`). Ученик без явных групп считается членом
группы по умолчанию (`schedule_group.is_default`) — так ученик, которого ещё не
разметили, не теряет запись.

Здесь же — ЕДИНСТВЕННОЕ место, где вычисляются «эффективные» группы ученика
(`effective_group_ids`). Все пути выбора слота обязаны звать его, а не
повторять условие у себя (память проекта: «общее условие звать функцией»).

Тарифная группа (`pricing_group_id`) у группы расписания — только подсказка:
назначение группы ученику деньги не двигает.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import delete, func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule_group import ScheduleGroup, UserScheduleGroup
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

AUDIENCES = ("kids", "adults")


async def list_groups(db: AsyncSession, *, include_inactive: bool = False) -> list[ScheduleGroup]:
    """Справочник групп: сначала по умолчанию, затем по названию."""
    stmt = select(ScheduleGroup)
    if not include_inactive:
        stmt = stmt.where(ScheduleGroup.is_active.is_(True))
    res = await db.execute(stmt.order_by(ScheduleGroup.is_default.desc(), ScheduleGroup.name))
    return list(res.scalars().all())


async def get_group(db: AsyncSession, group_id: int) -> ScheduleGroup:
    """Группа по id; 404, если её нет."""
    row = await db.get(ScheduleGroup, group_id)
    if row is None:
        raise DomainError(f"Группа расписания id={group_id} не найдена", status_code=404)
    return row


async def ensure_active_group(db: AsyncSession, group_id: int) -> ScheduleGroup:
    """Группа существует и не выключена — иначе 404/422."""
    row = await get_group(db, group_id)
    if not row.is_active:
        raise DomainError(f"Группа расписания «{row.name}» выключена", status_code=422)
    return row


async def default_group_id(db: AsyncSession) -> int:
    """id группы по умолчанию. Её отсутствие — поломка данных, не норма."""
    gid = (
        await db.execute(select(ScheduleGroup.id).where(ScheduleGroup.is_default.is_(True)))
    ).scalar_one_or_none()
    if gid is None:
        raise DomainError("Не задана группа расписания по умолчанию", status_code=500)
    return gid


def _validate_audience(audience: str) -> None:
    if audience not in AUDIENCES:
        raise DomainError(f"Аудитория должна быть одной из: {', '.join(AUDIENCES)}", status_code=422)


async def create_group(
    db: AsyncSession,
    *,
    audience: str,
    subject: str,
    name: str,
    pricing_group_id: Optional[int] = None,
) -> ScheduleGroup:
    """Завести группу. Название уникально — дубль даёт 409."""
    _validate_audience(audience)
    row = ScheduleGroup(
        audience=audience, subject=subject.strip(), name=name.strip(),
        pricing_group_id=pricing_group_id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise DomainError(f"Группа «{name}» уже есть или тарифная группа не найдена", status_code=409) from exc
    await db.commit()
    await db.refresh(row)
    logger.info("schedule_group создана: id=%s name=%s", row.id, row.name)
    return row


async def update_group(
    db: AsyncSession,
    group_id: int,
    *,
    audience: Optional[str] = None,
    subject: Optional[str] = None,
    name: Optional[str] = None,
    pricing_group_id: Optional[int] = None,
    clear_pricing_group: bool = False,
    is_active: Optional[bool] = None,
) -> ScheduleGroup:
    """Частичная правка. Группу по умолчанию выключить нельзя — на ней держатся
    все неразмеченные ученики и слоты, созданные без группы."""
    row = await get_group(db, group_id)
    if audience is not None:
        _validate_audience(audience)
        row.audience = audience
    if subject is not None:
        row.subject = subject.strip()
    if name is not None:
        row.name = name.strip()
    if clear_pricing_group:
        row.pricing_group_id = None
    elif pricing_group_id is not None:
        row.pricing_group_id = pricing_group_id
    if is_active is not None:
        if row.is_default and not is_active:
            raise DomainError("Группу по умолчанию выключить нельзя", status_code=422)
        row.is_active = is_active
    row.updated_at = func.now()
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise DomainError("Название группы занято или тарифная группа не найдена", status_code=409) from exc
    await db.commit()
    await db.refresh(row)
    return row


async def explicit_group_ids(db: AsyncSession, user_id: int) -> list[int]:
    """Явно назначенные группы ученика (без подстановки по умолчанию)."""
    res = await db.execute(
        select(UserScheduleGroup.group_id)
        .where(UserScheduleGroup.user_id == user_id)
        .order_by(UserScheduleGroup.group_id)
    )
    return list(res.scalars().all())


async def effective_group_ids(db: AsyncSession, user_id: int) -> list[int]:
    """Группы, слоты которых ученик видит: явные, иначе группа по умолчанию.

    Единственная точка правила «ученик без группы — детский» (tsk-1124).
    """
    # Выключенная группа не держит ученика: иначе он потерял бы все слоты.
    res = await db.execute(
        select(UserScheduleGroup.group_id)
        .join(ScheduleGroup, ScheduleGroup.id == UserScheduleGroup.group_id)
        .where(UserScheduleGroup.user_id == user_id, ScheduleGroup.is_active.is_(True))
        .order_by(UserScheduleGroup.group_id)
    )
    active = list(res.scalars().all())
    return active or [await default_group_id(db)]


def slot_visible(slot_group_id: int, group_ids: list[int]) -> bool:
    """Предикат видимости слота ученику (tsk-1124): группа слота среди
    эффективных групп ученика. Все пути ученика — запись, перенос, разовое
    занятие, выдача вариантов — решают через него, а не сравнивают сами."""
    return slot_group_id in group_ids


async def occurrence_group_ids(
    db: AsyncSession, slot_ids: list[Optional[int]]
) -> dict[Optional[int], int]:
    """Группа занятия по его ``slot_id`` — одним запросом на весь список.
    Ключ ``None`` (разовое занятие без слота) → группа по умолчанию, по тому же
    правилу, что `occurrence_visible`. Нужна фильтру занятий в кабинетах."""
    from app.models.lesson_slot import LessonSlot  # локально: модель слота тянет этот модуль

    real = sorted({sid for sid in slot_ids if sid is not None})
    result: dict[Optional[int], int] = {None: await default_group_id(db)}
    if real:
        rows = await db.execute(
            select(LessonSlot.id, LessonSlot.group_id).where(LessonSlot.id.in_(real))
        )
        result.update({sid: gid for sid, gid in rows.all()})
    return result


async def occurrence_visible(db: AsyncSession, slot_id: Optional[int], group_ids: list[int]) -> bool:
    """Видимо ли ученику занятие: группа его слота, а у занятия без слота
    (разовое) — группа по умолчанию, по тому же правилу, что ученик без группы."""
    from app.models.lesson_slot import LessonSlot  # локально: модель слота тянет этот модуль

    if slot_id is None:
        return slot_visible(await default_group_id(db), group_ids)
    slot_group = (
        await db.execute(select(LessonSlot.group_id).where(LessonSlot.id == slot_id))
    ).scalar_one_or_none()
    return slot_group is not None and slot_visible(slot_group, group_ids)


GROUP_MISMATCH_CODE = "schedule_group_mismatch"


async def guard_staff_slot_assignment(
    db: AsyncSession,
    student_id: int,
    slot_group_id: int,
    *,
    force: bool,
    added_by: Optional[int],
) -> None:
    """Методист ставит ученика в слот (добавление, перевод) — tsk-1124 Ф4.

    Группа слота среди групп ученика — пропускаем. Нет — 409 с машинным
    признаком ``schedule_group_mismatch``: экран спросит «добавить ученику группу?».
    С ``force`` группа слота ДОБАВЛЯЕТСЯ к эффективным группам ученика (а не
    заменяет их: ученик без явных групп иначе потерял бы группу по умолчанию).
    Коммит — на вызывающем. Несуществующий или не-ученик — 404/422 раньше
    вопроса про группу (иначе вставка упала бы на внешнем ключе с 500).
    """
    from app.services.lesson_calendar_service import ensure_user_has_role

    await ensure_user_has_role(db, student_id, "student")
    groups = await effective_group_ids(db, student_id)
    if slot_visible(slot_group_id, groups):
        return
    slot_group = await get_group(db, slot_group_id)
    if not force:
        raise DomainError(
            f"Ученик не в группе «{slot_group.name}». Добавить ему эту группу и поставить в слот?",
            status_code=409,
            payload={
                "code": GROUP_MISMATCH_CODE,
                "slot_group_id": slot_group_id,
                "student_group_ids": groups,
            },
        )
    for gid in sorted(set(groups) | {slot_group_id}):
        await db.execute(
            pg_insert(UserScheduleGroup)
            .values(user_id=student_id, group_id=gid, added_by=added_by)
            .on_conflict_do_nothing(index_elements=["user_id", "group_id"])
        )
    logger.info(
        "tsk-1124: ученику %s добавлена группа %s при постановке в слот (кем: %s)",
        student_id, slot_group_id, added_by,
    )


async def set_student_groups(
    db: AsyncSession,
    student_id: int,
    group_ids: list[int],
    *,
    added_by: Optional[int],
) -> list[int]:
    """Заменить набор групп ученика целиком. Пустой список — вернуть ученика в
    группу по умолчанию (строк нет). Уже привязанные слоты не трогаются —
    переносить ученика методист решает сам."""
    # Импорт внутри: lesson_calendar_service сам зовёт этот модуль.
    from app.services.lesson_calendar_service import ensure_user_has_role

    await ensure_user_has_role(db, student_id, "student")
    wanted = sorted(set(group_ids))
    for gid in wanted:
        await ensure_active_group(db, gid)
    await db.execute(
        delete(UserScheduleGroup).where(
            UserScheduleGroup.user_id == student_id,
            UserScheduleGroup.group_id.not_in(wanted) if wanted else true(),
        )
    )
    # ON CONFLICT: два одновременных PUT не должны падать 500 на дубле пары.
    for gid in wanted:
        await db.execute(
            pg_insert(UserScheduleGroup)
            .values(user_id=student_id, group_id=gid, added_by=added_by)
            .on_conflict_do_nothing(index_elements=["user_id", "group_id"])
        )
    await db.commit()
    logger.info("группы расписания ученика %s: %s (кем: %s)", student_id, wanted, added_by)
    return wanted


async def set_slot_group(db: AsyncSession, slot, group_id: int) -> None:
    """Проставить группу слоту (без коммита — зовётся из сервиса календаря)."""
    await ensure_active_group(db, group_id)
    slot.group_id = group_id


__all__ = [
    "AUDIENCES",
    "create_group",
    "default_group_id",
    "effective_group_ids",
    "ensure_active_group",
    "explicit_group_ids",
    "get_group",
    "list_groups",
    "occurrence_visible",
    "set_slot_group",
    "slot_visible",
    "set_student_groups",
    "update_group",
]
