"""tsk-591: пульс присутствия ученика в кабинете.

Кабинет (SPW) шлёт сюда короткий сигнал раз в две минуты, пока вкладка открыта
и видима. На этом сигнале держится различение, которое просил оператор:
«ученика нет в системе» против «открыл задание и молчит».

**Почему одна строка на ученика, а не событие в журнал.** Пульс частый по
своей природе: 30 учеников за полуторачасовое занятие дали бы ~1400 записей,
из которых нужна ровно последняя. Поэтому UPSERT в ``student_presence`` —
каждый ученик пишет свою строку, писатели не пересекаются, блокировка живёт
доли миллисекунды. Урок tsk-621 (запись на каждый запрос в общую строку —
мина) учтён именно так: общей строки здесь нет ни одной.

**Что такое ``interacted``.** Клиент ставит его, если за прошедший интервал
человек что-то делал руками: печатал, касался экрана, листал страницу. Без
этого признака ученик, вдумчиво читающий длинный материал, ничем не отличался
бы от ученика, который отошёл и оставил вкладку открытой, — и тревога
преподавателю приходила бы на каждый длинный текст.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Что открыто у ученика в момент пульса. Совпадает с CHECK-ограничением
#: таблицы: незнакомое значение упало бы ошибкой на записи, поэтому лишнее
#: сводим к ``other`` ещё на входе.
ALLOWED_CONTEXTS = frozenset({"task", "material", "course", "other"})


def normalize_context(context: Optional[str]) -> str:
    """Привести контекст к допустимому значению (незнакомое → ``other``)."""
    if context is None:
        return "other"
    value = context.strip().lower()
    return value if value in ALLOWED_CONTEXTS else "other"


async def touch(
    db: AsyncSession,
    student_id: int,
    *,
    interacted: bool,
    context: Optional[str] = None,
    course_id: Optional[int] = None,
    task_id: Optional[int] = None,
    material_id: Optional[int] = None,
) -> None:
    """Записать пульс ученика. Один UPSERT, без чтения перед записью.

    ``last_interaction_at`` двигается только при ``interacted=True``: пульс без
    взаимодействия говорит «вкладка открыта», но не «человек за экраном».

    Транзакцию не закрывает — коммитит вызывающий эндпоинт.
    """
    await db.execute(
        text(
            """
            INSERT INTO student_presence (
                student_id, last_seen_at, last_interaction_at,
                context, course_id, task_id, material_id, updated_at
            )
            VALUES (
                :student_id, now(),
                CASE WHEN :interacted THEN now() ELSE NULL END,
                :context, :course_id, :task_id, :material_id, now()
            )
            ON CONFLICT (student_id) DO UPDATE SET
                last_seen_at = now(),
                -- Прежняя отметка о взаимодействии не стирается пульсом без
                -- взаимодействия: иначе «читал, потом просто смотрит» мгновенно
                -- превращалось бы в «никогда ничего не делал».
                last_interaction_at = CASE
                    WHEN :interacted THEN now()
                    ELSE student_presence.last_interaction_at
                END,
                context = :context,
                course_id = :course_id,
                task_id = :task_id,
                material_id = :material_id,
                updated_at = now()
            """
        ),
        {
            "student_id": student_id,
            "interacted": bool(interacted),
            "context": normalize_context(context),
            "course_id": course_id,
            "task_id": task_id,
            "material_id": material_id,
        },
    )
