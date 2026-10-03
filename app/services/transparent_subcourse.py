"""Прозрачные подкурсы (tsk-1198): одно правило для всех обходов дерева.

Подкурс, прозрачный для родителя (`course_parents.is_transparent`), в дереве,
где этот родитель есть, отдельным разделом не показывается: его задания
считаются заданиями родителя-«хозяина» и встают в список хозяина на место
`tasks.host_order_position` (перед заданием хозяина с тем же `order_position`).
В другом дереве (практикум) тот же узел — обычный раздел.

Членство не меняется: подкурс остаётся в множестве курсов дерева, задание
по-прежнему принадлежит одному курсу (`tasks.course_id`), поэтому счёт по
множеству (`compute_course_state`, «N из M», ДЗ) двойного счёта не даёт.
Меняются только порядок и группировка — их и делают обходы через этот модуль.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_CACHE_KEY = "tsk1198_transparent_links"

# Ключ сортировки задания внутри списка курса. Четыре поля: (нет позиции,
# позиция, 0 — задание прозрачного подкурса / 1 — своё, id).
TaskKey = Tuple[int, int, int, int]


async def transparent_links(db: AsyncSession) -> dict[int, int]:
    """Все прозрачные связи `{подкурс: хозяин}`; кешируются на сессию БД.

    Связей десятки, поэтому один запрос на сессию дешевле точечных по узлам
    (обход дерева в next-item идёт по каждому узлу, tsk-662).
    """
    cached = db.info.get(_CACHE_KEY)
    if cached is not None:
        return cached
    rows = (
        await db.execute(
            text(
                "SELECT course_id, parent_course_id FROM course_parents "
                "WHERE is_transparent ORDER BY order_number ASC NULLS LAST, parent_course_id"
            )
        )
    ).fetchall()
    links: dict[int, int] = {}
    for child, host in rows:
        # Прозрачен узел может быть только для одного хозяина; при ошибке
        # разметки берём первого и пишем в лог, а не роняем обход.
        if int(child) in links:
            logger.warning("tsk-1198: подкурс %s прозрачен для нескольких родителей", child)
            continue
        links[int(child)] = int(host)
    db.info[_CACHE_KEY] = links
    return links


def reset_transparent_cache(db: AsyncSession) -> None:
    """Сбросить кеш связей (после правки иерархии в той же сессии)."""
    db.info.pop(_CACHE_KEY, None)


async def absorbed_in_tree(db: AsyncSession, tree_ids: Iterable[int]) -> dict[int, int]:
    """`{подкурс: хозяин}` для прозрачных подкурсов, чей хозяин есть в этом дереве."""
    ids = set(int(i) for i in tree_ids)
    links = await transparent_links(db)
    return {c: h for c, h in links.items() if c in ids and h in ids}


def task_key(
    order_position: Optional[int],
    task_id: int,
    *,
    host_order_position: Optional[int] = None,
    absorbed: bool = False,
) -> TaskKey:
    """Ключ задания в списке курса; паритет с SQL `order_position NULLS LAST, id`.

    Задание прозрачного подкурса (`absorbed`) сортируется по месту в хозяине
    и встаёт перед заданием хозяина с той же позицией.
    """
    if absorbed:
        pos = host_order_position
        return (0 if pos is not None else 1, pos or 0, 0, task_id)
    return (0 if order_position is not None else 1, order_position or 0, 1, task_id)
