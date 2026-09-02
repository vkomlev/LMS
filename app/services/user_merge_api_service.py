"""tsk-432: слияние профилей из кабинета — предпросмотр и применение.

Обёртка над уже существующим `user_merge_service`, которым до сих пор пользовался
только скрипт под протоколом `/db-check`. Кабинету нужны две вещи, которых у
скрипта нет:

1. **Предпросмотр.** Слияние необратимо и переносит данные по двум десяткам
   таблиц. Нажимать такую кнопку вслепую нельзя — сначала показываем, что
   именно переедет и сколько.
2. **Внятная причина отказа.** `merge_users` возвращает голое `False` на все
   случаи сразу (нет такого человека, тот же самый, уже слит). Для человека за
   экраном это бесполезно, поэтому проверки продублированы здесь — с разными
   сообщениями.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import user_merge_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

# Что показываем в предпросмотре человеческими словами. Берём из того же
# списка, по которому идёт перенос, — расходиться им нельзя.
_HUMAN_LABELS: dict[tuple[str, str], str] = {
    ("identity_link", "user_id"): "способы входа",
    ("attempts", "user_id"): "попытки решения",
    ("task_results", "user_id"): "результаты заданий",
    ("messages", "sender_id"): "отправленные сообщения",
    ("messages", "recipient_id"): "полученные сообщения",
    ("notifications", "user_id"): "уведомления",
    ("access_requests", "user_id"): "заявки на доступ",
    ("help_requests", "student_id"): "заявки на помощь",
    ("user_courses", "user_id"): "курсы ученика",
    ("user_roles", "user_id"): "роли",
    ("student_teacher_links", "student_id"): "привязки к преподавателям",
    ("student_teacher_links", "teacher_id"): "закреплённые ученики",
    ("lesson_slot_student", "student_id"): "слоты расписания",
    ("lesson_occurrence_participant", "student_id"): "занятия",
    ("teacher_courses", "teacher_id"): "курсы преподавателя",
    ("user_session", "user_id"): "открытые сеансы (будут закрыты)",
    ("student_curator", "student_id"): "кураторство над этим учеником",
    ("student_curator", "curator_id"): "ученики, за которых он отвечает",
    ("student_curator", "assigned_by"): "закрепления кураторов, сделанные им",
    ("student_curator", "ended_by"): "снятия кураторов, сделанные им",
}


@dataclass(frozen=True)
class MergeLine:
    """Одна строка предпросмотра."""

    table: str
    column: str
    label: str
    rows: int


@dataclass(frozen=True)
class MergePreview:
    """Что произойдёт при слиянии."""

    source_id: int
    source_name: Optional[str]
    target_id: int
    target_name: Optional[str]
    lines: list[MergeLine]
    total_rows: int


async def _assert_mergeable(db: AsyncSession, source_id: int, target_id: int) -> tuple:
    """Проверки с РАЗНЫМИ сообщениями — иначе человек не поймёт, что не так."""
    if source_id == target_id:
        raise DomainError("Нельзя слить учётную запись саму с собой", status_code=422)

    source = await user_merge_service.fetch_user(db, source_id)
    target = await user_merge_service.fetch_user(db, target_id)
    if source is None:
        raise DomainError(f"Учётная запись id={source_id} не найдена", status_code=404)
    if target is None:
        raise DomainError(f"Учётная запись id={target_id} не найдена", status_code=404)
    if not source.is_active:
        raise DomainError(
            f"Учётная запись id={source_id} уже слита в другую — сливать нечего",
            status_code=409,
        )
    if not target.is_active:
        raise DomainError(
            f"Учётная запись id={target_id} сама слита в другую. Выберите ту, "
            "которая останется рабочей",
            status_code=409,
        )
    return source, target


async def preview_merge(db: AsyncSession, *, source_id: int, target_id: int) -> MergePreview:
    """Что переедет из одной учётной записи в другую."""
    source, target = await _assert_mergeable(db, source_id, target_id)

    pairs = [(t, c) for t, c in user_merge_service.SIMPLE_MOVES]
    pairs += [(t, c) for t, c, _ in user_merge_service.CONFLICT_MOVES]
    # Сеансы не переезжают, а закрываются — человек будет вынужден войти
    # заново. Показываем это отдельной строкой, чтобы не выглядело потерей.
    pairs += [(t, c) for t, c in user_merge_service.DELETE_ON_MERGE]
    # tsk-742: периоды кураторства переносятся отдельным шагом (частичная
    # уникальность «один действующий куратор»), в общие списки они не входят —
    # и потому не попали бы в предпросмотр. А это ровно то, о чём человек за
    # экраном обязан знать заранее: кто за кого отвечает.
    pairs += [("student_curator", "student_id"), ("student_curator", "curator_id")]

    lines: list[MergeLine] = []
    seen: set[tuple[str, str]] = set()
    for table, column in pairs:
        if (table, column) in seen:
            continue
        seen.add((table, column))
        rows = await user_merge_service.count_rows(db, table, column, source_id)
        if not rows:
            continue  # пустые строки в предпросмотре только зашумляют
        lines.append(
            MergeLine(
                table=table,
                column=column,
                label=_HUMAN_LABELS.get((table, column), f"{table}.{column}"),
                rows=rows,
            )
        )
    lines.sort(key=lambda line: (-line.rows, line.label))
    return MergePreview(
        source_id=source_id,
        source_name=source.full_name,
        target_id=target_id,
        target_name=target.full_name,
        lines=lines,
        total_rows=sum(line.rows for line in lines),
    )


async def merge(db: AsyncSession, *, source_id: int, target_id: int, actor_id: Optional[int]) -> None:
    """Слить и зафиксировать. Операция необратима."""
    await _assert_mergeable(db, source_id, target_id)
    merged = await user_merge_service.merge_users(db, source_id=source_id, target_id=target_id)
    if not merged:
        # Сюда попасть уже не должны — проверки выше их перехватывают. Значит
        # что-то изменилось между проверкой и записью; молча «успех» вернуть нельзя.
        raise DomainError(
            "Слияние не выполнено — состояние учётных записей изменилось. "
            "Обновите страницу и попробуйте снова",
            status_code=409,
        )
    await db.commit()
    logger.info(
        "tsk-432 слияние: source=%s target=%s кем=%s", source_id, target_id, actor_id
    )
