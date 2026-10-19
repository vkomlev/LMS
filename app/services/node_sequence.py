"""Порядок элементов узла курса — одно правило для всех мест обхода (tsk-996).

ADR-0008 (`docs/ai/adr/0008-interleave-theory-practice-anchor.md`), фаза A
постановки `docs/briefs/2026-10-19-p7-anchor-implementation.md`.

До этого модуля порядок «материалы узла → подразделы → задания узла» был
записан шесть раз: обход движка (`_walk_tree`, курсор next-item), программа
курса (`me_service` syllabus-states), правило «ушёл вперёд»
(`content_grace_service._walk_sequence`), предпросмотр (`preview_navigation`),
карточка прогресса ученика у преподавателя (`manual_progress_service`). Любая
правка порядка — а фаза B вставляет задания после их материала — требовала
бы шести согласованных правок. Теперь порядок задаёт только этот модуль.

Как устроено. Каждый элемент узла получает **ключ последовательности** —
кортеж, сравнение которого и есть учебный порядок внутри узла. Первая
составляющая ключа — часть узла:

* ``SEG_HEAD`` — голова: материалы узла (в фазе B — и задания, привязанные
  якорем к материалу, сразу после него);
* ``SEG_CHILDREN`` — подразделы узла по ``order_number`` (tsk-1250);
* ``SEG_TAIL`` — хвост: задания узла (в фазе B — задания без якоря).

Голова и хвост разделены подразделами, поэтому обход дерева идёт шагами
``(course_id, HEAD)`` и ``(course_id, TAIL)`` (`walk_steps`). Курсор
продолжения (next-item с ``after_*``) сравнивает ключи, а не тип элемента:
шаг позиции — часть, в которую попал её ключ, дальше по шагу — всё, у чего
ключ больше. Поэтому смешанная последовательность фазы B не потребует ни
нового курсора, ни ветвления по типу позиции.

В фазе A якорей нет, и функция обязана давать ровно прежний порядок; это
проверяет `tests/test_tsk996_node_sequence.py` (выдача при
``NODE_SEQUENCE_ENABLED=true`` и ``false`` совпадает).

Модуль чистый: ни запросов к БД, ни сессии. Данные узла загружает
вызывающий — каждый своим (уже существующим) запросом, поэтому число
запросов не меняется.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import (
    Awaitable,
    Callable,
    Generator,
    Iterable,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
)

from app.services.transparent_subcourse import TaskKey

ItemKind = Literal["material", "task"]
Segment = Literal["head", "tail"]

HEAD: Segment = "head"
TAIL: Segment = "tail"

SEG_HEAD = 0
SEG_CHILDREN = 1
SEG_TAIL = 2

# Ключ последовательности внутри узла: (часть, ...); сравнивается как кортеж.
SeqKey = tuple
# Шаг обхода дерева: (course_id, часть узла).
Step = Tuple[int, Segment]
# Подраздел узла: (child_id, order_number).
ChildRow = Tuple[int, Optional[int]]


def node_sequence_enabled() -> bool:
    """Рубильник `NODE_SEQUENCE_ENABLED` (по умолчанию включён).

    ``false`` возвращает все шесть мест к прежнему коду без выката (откат —
    смена окружения и рестарт, ADR-0008 «Переключатель на время перехода»).
    Читается из окружения на каждый вызов, как и другие рубильники обхода
    (`content_grace_service.read_from_overrides_enabled`): `Settings()`
    целиком здесь не нужен.
    """
    return os.getenv("NODE_SEQUENCE_ENABLED", "true").lower() in ("true", "1", "yes")


def order_key(order_position: Optional[int], item_id: int) -> Tuple[int, int, int]:
    """Паритет с SQL `order_position ASC NULLS LAST, id ASC`."""
    return (0 if order_position is not None else 1, order_position or 0, item_id)


def material_seq_key(
    order_position: Optional[int], material_id: int, course_id: int
) -> SeqKey:
    """Ключ материала в последовательности узла.

    ``course_id`` — курс, которому материал принадлежит на самом деле. У
    своего материала узла он один и тот же и на порядок не влияет. Он нужен
    материалам прозрачного подкурса (tsk-1198), которые программа курса и
    карточка прогресса показывают в списке хозяина: там они всегда шли
    группой по курсу (SQL `ORDER BY course_id, order_position, id`), и ключ
    сохраняет этот порядок.
    """
    return (SEG_HEAD, course_id, order_key(order_position, material_id))


def task_seq_key(key: TaskKey) -> SeqKey:
    """Ключ задания: в фазе A якорей нет — задание в хвосте узла.

    ``key`` — `transparent_subcourse.task_key` (у задания прозрачного
    подкурса — место в хозяине).
    """
    return (SEG_TAIL, key)


def child_seq_key(order_number: Optional[int], child_id: int) -> SeqKey:
    """Ключ подраздела: по `order_number ASC NULLS LAST, id` (tsk-1250)."""
    return (SEG_CHILDREN, order_key(order_number, child_id))


def segment_of(key: SeqKey) -> Segment:
    """Часть узла (шаг обхода), в которую попадает элемент с этим ключом."""
    if key[0] == SEG_HEAD:
        return HEAD
    if key[0] == SEG_TAIL:
        return TAIL
    raise ValueError(f"ключ подраздела не принадлежит шагу элементов: {key!r}")


@dataclass(frozen=True)
class NodeEntry:
    """Элемент последовательности узла."""

    kind: ItemKind
    item_id: int
    key: SeqKey


def node_sequence(
    *,
    materials: Iterable[Tuple[int, Optional[int], int]] = (),
    tasks: Iterable[Tuple[int, TaskKey]] = (),
) -> List[NodeEntry]:
    """Элементы одного узла в учебном порядке: ``[(kind, id)]`` с ключами.

    Args:
        materials: ``(material_id, order_position, course_id)`` материалов узла.
        tasks: ``(task_id, task_key)`` заданий узла (с влитыми заданиями
            прозрачных подкурсов, если вызывающий их показывает).

    Подразделы в список не входят: их место (между головой и хвостом) задаёт
    обход дерева `walk_steps`. Чтобы отдать один шаг, вызывающий берёт
    элементы с ``segment_of(entry.key) == шаг``.

    Фаза B добавит сюда якорь задания: задание с якорем получит ключ своего
    материала с хвостиком ``(1, task_key)`` и встанет сразу после него.
    """
    entries = [
        NodeEntry("material", int(mid), material_seq_key(op, int(mid), int(cid)))
        for mid, op, cid in materials
    ]
    entries.extend(NodeEntry("task", int(tid), task_seq_key(key)) for tid, key in tasks)
    entries.sort(key=lambda e: e.key)
    return entries


def segment_entries(entries: Iterable[NodeEntry], segment: Segment) -> List[NodeEntry]:
    """Элементы последовательности, относящиеся к одному шагу узла."""
    return [e for e in entries if segment_of(e.key) == segment]


def _walk_plan(root_id: int) -> Generator[int, Sequence[ChildRow], List[Step]]:
    """Обход дерева шагами узлов; подразделы узла запрашиваются у вызывающего.

    Генератор отдаёт ``course_id`` и ждёт в ответ его подразделы — так один и
    тот же обход работает и с ленивой загрузкой из БД (движок: запрос на узел,
    в том же порядке, что и прежде), и с деревом, загруженным целиком
    (правило «ушёл вперёд»).

    Порядок прежний (tsk-127/1250/261): голова узла, затем подразделы по
    `child_seq_key` (каждый целиком), затем хвост узла. Узел, висящий под
    несколькими родителями, обходится по первому вхождению.
    """
    steps: List[Step] = []
    seen: set[int] = set()
    # Стек «что сделать»: (course_id, None) — раскрыть узел, (course_id, TAIL) —
    # выдать хвост узла после его подразделов.
    stack: List[Tuple[int, Optional[Segment]]] = [(root_id, None)]
    while stack:
        course_id, action = stack.pop()
        if action == TAIL:
            steps.append((course_id, TAIL))
            continue
        if course_id in seen:
            continue
        seen.add(course_id)
        steps.append((course_id, HEAD))
        children = yield course_id
        stack.append((course_id, TAIL))
        ordered = sorted(children or (), key=lambda c: child_seq_key(c[1], int(c[0])))
        for child_id, _order in reversed(ordered):
            stack.append((int(child_id), None))
    return steps


def walk_steps(root_id: int, children_of: Callable[[int], Sequence[ChildRow]]) -> List[Step]:
    """Шаги обхода дерева, загруженного целиком (``children_of`` без запросов)."""
    plan = _walk_plan(root_id)
    try:
        course_id = next(plan)
        while True:
            course_id = plan.send(children_of(course_id))
    except StopIteration as done:
        return done.value


async def walk_steps_async(
    root_id: int, load_children: Callable[[int], Awaitable[Sequence[ChildRow]]]
) -> List[Step]:
    """Шаги обхода дерева с ленивой загрузкой подразделов (запрос на узел)."""
    plan = _walk_plan(root_id)
    try:
        course_id = next(plan)
        while True:
            course_id = plan.send(await load_children(course_id))
    except StopIteration as done:
        return done.value


def post_order_courses(steps: Iterable[Step]) -> List[int]:
    """Курсы дерева в post-order (tsk-127): узел — после всех своих подразделов.

    Хвост узла стоит ровно там, где прежний обход дописывал узел в список
    курсов, поэтому список совпадает с `_collect_courses_in_order`.
    """
    return [cid for cid, segment in steps if segment == TAIL]


__all__ = [
    "HEAD",
    "TAIL",
    "NodeEntry",
    "Step",
    "child_seq_key",
    "material_seq_key",
    "node_sequence",
    "node_sequence_enabled",
    "order_key",
    "post_order_courses",
    "segment_entries",
    "segment_of",
    "task_seq_key",
    "walk_steps",
    "walk_steps_async",
]
