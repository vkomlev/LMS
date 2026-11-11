"""Жёсткая последовательность подкурсов — гейт для next-item (tsk-1500).

Настройка `courses.strict_progression` на КОРНЕ курса: пока в предыдущих
подкурсах есть нерешённые обязательные задания, движок не отдаёт элементы
следующих — возвращает `blocked_dependency` с разделом, который надо закрыть
(экран ученика для этого типа есть с tsk-231, название и uid раздела едут
в dependency_course_title/uid).

«Решено» = семантика PASSED движка: последняя сданная попытка по submitted_at,
score * 2 >= max_score (та же граница 0.5, что в learning_engine_service).
Ручная разблокировка методистом — строка в `progression_overrides`: пока она
есть, гейт для пары (корень, ученик) молчит.

Дешевизна: запросы гейта идут только на переходе через границу подкурса;
next-item внутри одного подкурса их не делает вовсе.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Первый узел из списка с нерешёнными обязательными заданиями. Один запрос
# на все предыдущие узлы; порядок списка (порядок обхода движка) решает,
# какой узел называть блокирующим.
_FIRST_LOCKED_SQL = """
WITH last_score AS (
    SELECT t.id, t.course_id, t.max_score,
           (SELECT tr.score FROM task_results tr
             WHERE tr.task_id = t.id AND tr.user_id = :uid
             ORDER BY tr.submitted_at DESC LIMIT 1) AS score
    FROM tasks t
    WHERE t.course_id = ANY(:prior_ids)
      AND t.requirement_level = 'required'
      AND t.is_active
)
SELECT course_id, count(*) AS incomplete
FROM last_score
WHERE score IS NULL OR max_score <= 0 OR score * 2 < max_score
GROUP BY course_id
"""


async def strict_progression_enabled(db: AsyncSession, root_course_id: int) -> bool:
    """Настройка жёсткой последовательности на корне курса (по умолчанию выключена)."""
    row = await db.execute(
        text("SELECT strict_progression FROM courses WHERE id = :id"),
        {"id": root_course_id},
    )
    return bool(row.scalar())


async def has_override(db: AsyncSession, root_course_id: int, student_id: int) -> bool:
    """Ручная разблокировка методистом для конкретного ученика."""
    row = await db.execute(
        text(
            "SELECT 1 FROM progression_overrides "
            "WHERE root_course_id = :root AND user_id = :user"
        ),
        {"root": root_course_id, "user": student_id},
    )
    return row.first() is not None


async def first_locked_node(
    db: AsyncSession, student_id: int, prior_node_ids: list[int]
) -> Optional[int]:
    """Первый по порядку узел с нерешёнными обязательными заданиями.

    Returns:
        ID блокирующего узла или None — гейт чист, переход разрешён.
    """
    if not prior_node_ids:
        return None
    rows = await db.execute(
        text(_FIRST_LOCKED_SQL),
        {"uid": student_id, "prior_ids": list(prior_node_ids)},
    )
    by_node = {int(r[0]): int(r[1]) for r in rows.fetchall()}
    for node_id in prior_node_ids:
        if node_id in by_node:
            logger.info(
                "strict_progression: student=%s держит узел=%s (%s заданий)",
                student_id, node_id, by_node[node_id],
            )
            return node_id
    return None
