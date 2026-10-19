"""Следующий элемент курса для предпросмотра «глазами ученика» (tsk-1334).

Преподаватель/методист проходит курс как ученик, но без прогресса: «Я прошёл
материал» и верный ответ ведут к следующему элементу по учебному порядку
дерева. Прогресс, зависимости, замки и выборка по сложности не учитываются,
ничего не пишется.

Порядок — тот же, что у движка next-item после tsk-1250
(`LearningEngineService._walk_tree`): материалы узла, затем его подразделы,
затем задания узла; прозрачный подкурс идёт в списке хозяина (tsk-1198).
В обход попадают те же уровни, что видит ученик (`required`, `skippable`);
текущий элемент находится и тогда, когда он `recommended`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, List, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.association_tables import t_course_parents
from app.models.materials import Materials
from app.models.tasks import Tasks
from app.services.learning_engine_service import LearningEngineService
from app.services.node_sequence import (
    HEAD,
    node_sequence,
    node_sequence_enabled,
    segment_entries,
)
from app.services.transparent_subcourse import task_key, transparent_links

logger = logging.getLogger(__name__)

ItemKind = Literal["material", "task"]
_WALK_LEVELS = ("required", "skippable")
# Защита от петли в course_parents при подъёме к корню.
_MAX_DEPTH = 64


@dataclass(frozen=True)
class PreviewNext:
    """Итог: корень обхода и следующий элемент (None — курс закончился)."""

    root_course_id: int
    next_kind: Optional[ItemKind]
    next_id: Optional[int]


async def _item_course_id(db: AsyncSession, kind: ItemKind, item_id: int) -> Optional[int]:
    """Курс элемента или None, если элемента нет."""
    model = Materials if kind == "material" else Tasks
    row = (await db.execute(select(model.course_id).where(model.id == item_id))).first()
    return int(row[0]) if row is not None and row[0] is not None else None


async def _top_root(db: AsyncSession, course_id: int) -> int:
    """Корень дерева над курсом: первый родитель по порядку, пока он есть."""
    current = course_id
    seen = {current}
    for _ in range(_MAX_DEPTH):
        row = (
            await db.execute(
                select(t_course_parents.c.parent_course_id)
                .where(t_course_parents.c.course_id == current)
                .order_by(
                    t_course_parents.c.order_number.asc().nulls_last(),
                    t_course_parents.c.parent_course_id.asc(),
                )
                .limit(1)
            )
        ).first()
        if row is None or int(row[0]) in seen:
            return current
        current = int(row[0])
        seen.add(current)
    return current


def _ordered_items(
    db: AsyncSession, engine: LearningEngineService, root_id: int
) -> AsyncIterator[Tuple[ItemKind, int, bool]]:
    """Активные элементы дерева по учебному порядку: (вид, id, в обходе ли).

    Лениво, по шагу: узлы после найденного следующего элемента не читаются.
    tsk-996: порядок узла — общая `node_sequence`; при
    NODE_SEQUENCE_ENABLED=false — прежний код (`_ordered_items_legacy`).
    """
    if node_sequence_enabled():
        return _ordered_items_by_sequence(db, engine, root_id)
    return _ordered_items_legacy(db, engine, root_id)


async def _ordered_items_by_sequence(
    db: AsyncSession, engine: LearningEngineService, root_id: int
) -> AsyncIterator[Tuple[ItemKind, int, bool]]:
    """`_ordered_items` по шагам `node_sequence` (tsk-996).

    Шаг узла — его голова (материалы) или хвост (задания со влитыми заданиями
    прозрачных подкурсов, tsk-1198); каждый шаг читает только свою часть, как
    и прежде. Фаза B (якоря) добавит в голову задания узла.
    """
    steps = await engine._collect_node_steps(db, root_id)  # noqa: SLF001
    tree = {cid for cid, _ in steps}
    links = await transparent_links(db)
    absorbed_here = {c for c, h in links.items() if c in tree and h in tree}

    for cid, segment in steps:
        if cid in absorbed_here:
            continue
        eligible: dict[Tuple[str, int], bool] = {}
        if segment == HEAD:
            rows = (
                await db.execute(
                    select(Materials.id, Materials.order_position, Materials.requirement_level)
                    .where(Materials.course_id == cid, Materials.is_active.is_(True))
                )
            ).all()
            materials = []
            for r in rows:
                materials.append((int(r[0]), r[1], cid))
                eligible[("material", int(r[0]))] = r[2] in _WALK_LEVELS
            seq = node_sequence(materials=materials)
        else:
            absorbed = [c for c, h in links.items() if h == cid and c in tree]
            rows = (
                await db.execute(
                    select(
                        Tasks.id, Tasks.order_position, Tasks.host_order_position,
                        Tasks.course_id, Tasks.requirement_level,
                    ).where(Tasks.course_id.in_([cid, *absorbed]), Tasks.is_active.is_(True))
                )
            ).all()
            tasks = []
            for r in rows:
                tasks.append((
                    int(r[0]),
                    task_key(
                        r[1], int(r[0]),
                        host_order_position=r[2],
                        absorbed=int(r[3]) != cid,
                    ),
                ))
                eligible[("task", int(r[0]))] = r[4] in _WALK_LEVELS
            seq = node_sequence(tasks=tasks)
        for entry in segment_entries(seq, segment):
            yield entry.kind, entry.item_id, eligible[(entry.kind, entry.item_id)]


async def _ordered_items_legacy(
    db: AsyncSession, engine: LearningEngineService, root_id: int
) -> AsyncIterator[Tuple[ItemKind, int, bool]]:
    """Прежний `_ordered_items` — путь NODE_SEQUENCE_ENABLED=false (tsk-996)."""
    steps = await engine._collect_steps_in_order(db, root_id)
    tree = {cid for cid, _ in steps}
    links = await transparent_links(db)
    absorbed_here = {c for c, h in links.items() if c in tree and h in tree}

    for cid, step in steps:
        if cid in absorbed_here:
            continue
        if step == "materials":
            rows = (
                await db.execute(
                    select(Materials.id, Materials.requirement_level)
                    .where(Materials.course_id == cid, Materials.is_active.is_(True))
                    .order_by(Materials.order_position.asc().nulls_last(), Materials.id.asc())
                )
            ).all()
            for r in rows:
                yield "material", int(r[0]), r[1] in _WALK_LEVELS
        else:
            absorbed = [c for c, h in links.items() if h == cid and c in tree]
            rows = (
                await db.execute(
                    select(
                        Tasks.id, Tasks.order_position, Tasks.host_order_position,
                        Tasks.course_id, Tasks.requirement_level,
                    ).where(Tasks.course_id.in_([cid, *absorbed]), Tasks.is_active.is_(True))
                )
            ).all()
            keyed = sorted(
                (
                    task_key(
                        r[1], int(r[0]),
                        host_order_position=r[2],
                        absorbed=int(r[3]) != cid,
                    ),
                    int(r[0]),
                    r[4] in _WALK_LEVELS,
                )
                for r in rows
            )
            for _key, tid, eligible in keyed:
                yield "task", tid, eligible


async def preview_next_item(
    db: AsyncSession,
    kind: ItemKind,
    item_id: int,
    root_course_id: Optional[int] = None,
) -> Optional[PreviewNext]:
    """Следующий элемент после (kind, item_id) в дереве корня.

    Корень не передан или элемента в его дереве нет — берётся корень над
    курсом самого элемента. None — элемента не существует.
    """
    course_id = await _item_course_id(db, kind, item_id)
    if course_id is None:
        return None
    engine = LearningEngineService()

    roots: List[int] = []
    if root_course_id is not None:
        roots.append(root_course_id)
    fallback = await _top_root(db, course_id)
    if fallback not in roots:
        roots.append(fallback)

    for root in roots:
        found_current = False
        async for k, iid, eligible in _ordered_items(db, engine, root):
            if found_current and eligible:
                return PreviewNext(root, k, iid)
            if k == kind and iid == item_id:
                found_current = True
        if found_current:
            return PreviewNext(root, None, None)
        logger.info("preview_next_item: %s %s нет в дереве корня %s", kind, item_id, root)

    # Элемент вне любого дерева (например, неактивен) — дальше идти некуда.
    return PreviewNext(fallback, None, None)
