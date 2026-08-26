"""tsk-692: содержимое, добавленное после прохождения темы, не становится долгом.

# Что именно решается

Раньше всё, что методист добавлял в курс ПОСЛЕ того, как ученик прошёл тему,
немедленно превращалось в его долг: человек закрыл раздел, а через неделю в нём
снова горит «не сделано». Чинилось это руками оператора — то есть не чинилось.
Живые случаи разбирались в tsk-690 (материалы-«Вопросы», подкурс «Черепашья
графика», «Кэш и мемоизация» в курсе «Рекурсия в Python»).

# Правило (выбрано оператором 27.08)

Новое обязательное содержимое приходит тому, кто тему уже прошёл, **как
рекомендуемое**: он видит, что появилось новое, но это не долг. Для того, кто
тему не проходил, оно остаётся **обязательным**.

Отклонённые варианты и почему: «не показывать прошедшим вовсе» — важное
дополнение к теме они бы никогда не увидели; «долг только по явной отметке при
правке» — требует действия человека при каждой правке, а такие правила у нас
дважды не сработали (tsk-495, tsk-669).

# Где живёт правило и почему здесь

В вычислении учебного пути, а не в момент правки курса и не в самой отметке
уровня обязательности:

* `requirement_level` — одна отметка на всех учеников сразу; «обязательно для
  Пети и рекомендуемо для Маши» ею не выразить в принципе;
* «в момент правки курса» пришлось бы при каждом добавлении элемента обойти всех
  учеников курса и записать каждому персональное исключение. Точек, где
  содержимое появляется, несколько (пакетный импорт, экран методиста, скрипты
  правки данных) — пропустишь одну, и правило молча не сработает. Это ровно тот
  класс, на котором мы уже дважды обожглись;
* здесь знание о прошлом ученика уже под рукой: движок и так считает, что
  пройдено. Новое знание нужно ровно одно — «когда элемент появился», и это
  свойство самого элемента, а не ученика.

# Что считается «уже прошёл»

Не курс целиком и не «тема» в методическом смысле, а **узел дерева курсов** —
тот курс, в котором лежит элемент, плюс его предки (это нужно, когда добавили
не элемент, а целый подкурс: у нового узла своих закрытых элементов нет, и
судить можно только по родителю).

Для узла берётся `T` — время последнего засчитанного ученику элемента этого узла
(с учётом всего поддерева). Узел считается пройденным на момент `T`, если **все**
его сейчас незакрытые обязательные элементы появились позже `T`. Если хоть один
незакрытый элемент старше `T` — значит ученик узел не закрывал, и правило не
применяется вовсе.

Отсюда обратный случай выполняется по построению: у не проходившего либо нет
засчитанных элементов узла (`T` не определён), либо среди незакрытых есть старые
— в обоих случаях правило молчит и новое содержимое остаётся обязательным.

# Чего правило НЕ делает

Оно ничего не пересчитывает задним числом и ничего не закрывает за ученика:
баллы, попытки и отметки о прохождении не трогаются. Оно только снимает
обязательность — то есть может лишь убрать долг, но не добавить.

`tasks.created_at` у заданий, заведённых до tsk-692, равен NULL и читается как
«существовало всегда»: такие задания правило не прощает никогда и, попав в
незакрытые, блокируют прощение всего узла. В день выката это даёт ноль изменений
по заданиям — накопленное чинилось разово (tsk-690), правило чинит будущее.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_GRACE_CACHE_KEY = "tsk692_content_grace_cache"

# Порог зачёта задания. Держим то же значение, что в learning_engine_service:
# импортировать оттуда нельзя — движок сам зовёт этот модуль (кольцо импорта).
_PASS_THRESHOLD_RATIO = 0.5

# Уровни, которые движок считает обязательными к прохождению. Ровно тот же
# набор, что в фильтрах learning_engine_service и me_service.
_REQUIRED_LEVELS = ("required", "skippable")


@dataclass(frozen=True)
class GracedItems:
    """Элементы, которые ученику показываются рекомендуемыми вместо обязательных."""

    tasks: frozenset[int] = frozenset()
    materials: frozenset[int] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.tasks or self.materials)


EMPTY_GRACE = GracedItems()


@dataclass
class _Item:
    """Один обязательный элемент узла глазами правила."""

    kind: str  # "task" | "material"
    item_id: int
    course_id: int
    created_at: Optional[datetime]  # None — «существовало всегда»
    done: bool  # ученик элемент закрыл
    done_at: Optional[datetime]  # когда закрыл; None при done=True — неизвестно


@dataclass
class _Node:
    """Узел дерева: свои элементы + собранные из поддерева."""

    own: List[_Item] = field(default_factory=list)
    children: List[int] = field(default_factory=list)


def grace_cache(db: AsyncSession) -> Dict[Tuple[int, int], GracedItems]:
    """Кеш правила, живущий ровно столько же, сколько сессия БД.

    Ключ — (ученик, корень дерева). За один HTTP-запрос движок обходит одно и то
    же дерево по многу раз (см. tsk-662), и без кеша каждый обход стоил бы трёх
    лишних запросов.
    """
    cache = db.info.get(_GRACE_CACHE_KEY)
    if cache is None:
        cache = {}
        db.info[_GRACE_CACHE_KEY] = cache
    return cache


def invalidate_grace_cache(db: AsyncSession) -> None:
    """Сбросить кеш правила: в этой сессии изменилось содержимое или прогресс."""
    db.info.pop(_GRACE_CACHE_KEY, None)


# Рёбра дерева и все обязательные элементы каждого узла — одним запросом на
# дерево. `path` защищает от цикла в `course_parents` тем же приёмом, что и
# остальные рекурсивные обходы в проекте (иначе запрос не вернул бы неверный
# ответ, а ушёл бы в бесконечную рекурсию).
_TREE_EDGES_SQL = """
WITH RECURSIVE tree AS (
    SELECT CAST(:root_id AS integer) AS node_id,
           ARRAY[CAST(:root_id AS integer)] AS path
    UNION ALL
    SELECT cp.course_id, t.path || cp.course_id
    FROM tree t
    JOIN course_parents cp ON cp.parent_course_id = t.node_id
    WHERE NOT cp.course_id = ANY(t.path)
)
SELECT DISTINCT cp.parent_course_id AS parent_id, cp.course_id AS child_id
FROM course_parents cp
WHERE cp.parent_course_id IN (SELECT node_id FROM tree)
  AND cp.course_id IN (SELECT node_id FROM tree)
"""

_TREE_NODES_SQL = """
WITH RECURSIVE tree AS (
    SELECT CAST(:root_id AS integer) AS node_id,
           ARRAY[CAST(:root_id AS integer)] AS path
    UNION ALL
    SELECT cp.course_id, t.path || cp.course_id
    FROM tree t
    JOIN course_parents cp ON cp.parent_course_id = t.node_id
    WHERE NOT cp.course_id = ANY(t.path)
)
SELECT DISTINCT node_id FROM tree
"""

# Обязательные элементы дерева с датой появления и временем зачёта у ученика.
#
# Задание засчитано, если ПОСЛЕДНИЙ его результат — проходной (та же логика, что
# в compute_course_state) либо оно помечено пропущенным. Материал засчитан по
# student_material_progress. Время зачёта — момент, по которому и определяется,
# что было «до», а что «после».
#
# Признак «засчитано» (`done`) и время зачёта (`done_at`) — ДВЕ РАЗНЫЕ колонки, и
# это принципиально. Подставить зачёту без отметки времени условный ноль
# (`epoch`) нельзя: `T` узла упало бы в 1970, любой элемент курса оказался бы
# «новее последнего зачёта», и правило простило бы узел целиком — то есть сняло
# бы обязательность там, где ученик просто не дошёл. На боевой базе таких строк
# сегодня нет (проверено 27.08: 7264 зачтённых материала, ни одного без времени),
# но появиться они могут — например ручным зачётом мимо сервиса. Поэтому зачёт с
# неизвестным временем читается как «когда закрыл — неизвестно» и отменяет
# прощение узла, а не занижает его границу.
_TREE_ITEMS_SQL = """
/* tsk692-grace-items */
WITH last_per_task AS (
    SELECT DISTINCT ON (tr.task_id)
        tr.task_id, tr.score, tr.max_score, tr.submitted_at
    FROM task_results tr
    INNER JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
    WHERE tr.user_id = :student_id
      AND tr.task_id IN (
          SELECT id FROM tasks
          WHERE course_id = ANY(:tree_ids)
            AND is_active = true
            AND requirement_level = ANY(:levels)
      )
    ORDER BY tr.task_id, tr.submitted_at DESC, tr.id DESC
)
SELECT
    'task' AS kind,
    t.id AS item_id,
    t.course_id,
    t.created_at,
    (
        stp.status = 'skipped'
        OR (
            lp.max_score > 0
            AND lp.score::float / lp.max_score >= :pass_threshold
        )
    ) AS done,
    CASE
        WHEN stp.status = 'skipped' THEN COALESCE(stp.skipped_at, stp.updated_at)
        WHEN lp.max_score > 0
             AND lp.score::float / lp.max_score >= :pass_threshold
            THEN lp.submitted_at
    END AS done_at
FROM tasks t
LEFT JOIN last_per_task lp ON lp.task_id = t.id
LEFT JOIN student_task_progress stp
    ON stp.task_id = t.id AND stp.student_id = :student_id
WHERE t.course_id = ANY(:tree_ids)
  AND t.is_active = true
  AND t.requirement_level = ANY(:levels)
UNION ALL
SELECT
    'material' AS kind,
    m.id,
    m.course_id,
    m.created_at,
    (smp.status IN ('completed', 'skipped')) AS done,
    CASE
        WHEN smp.status IN ('completed', 'skipped')
            THEN COALESCE(smp.completed_at, smp.skipped_at)
    END
FROM materials m
LEFT JOIN student_material_progress smp
    ON smp.material_id = m.id AND smp.student_id = :student_id
WHERE m.course_id = ANY(:tree_ids)
  AND m.is_active = true
  AND m.requirement_level = ANY(:levels)
"""


async def compute_graced_items(
    db: AsyncSession,
    student_id: int,
    root_course_id: int,
) -> GracedItems:
    """Элементы дерева `root_course_id`, переставшие быть долгом для ученика.

    :param db: сессия БД.
    :param student_id: ID ученика.
    :param root_course_id: корень дерева курса. Передавать надо максимально
        верхний известный узел: правило смотрит и на предков, а выше
        переданного корня оно их не увидит.

        Отсюда известное ограничение. `compute_course_state` зовётся и для
        отдельных подкурсов (проверка `course_dependencies`), и там правило
        видит только поддерево этого подкурса. Если элемент прощён благодаря
        ПРЕДКУ подкурса — а так бывает ровно в одном случае, когда добавили
        целый новый подкурс без единого закрытого элемента, — состояние такого
        подкурса до COMPLETED не дойдёт, и зависимость на него останется
        запертой. Это не хуже, чем сегодня (замок и так висит), а не лучше;
        всё, что ученик видит сам — «следующий шаг», программа курса, процент на
        карточке — считается от корня и правилом покрыто. На 27.08 на проде
        случаев нет: единственное прощение живёт в курсе 1451, зависимостей на
        него нет ни одной.
    :returns: ID заданий и материалов, которые ученику показываются
        рекомендуемыми вместо обязательных. Пустой набор — правило не
        сработало нигде, поведение прежнее.
    """
    cache = grace_cache(db)
    cached = cache.get((student_id, root_course_id))
    if cached is not None:
        return cached

    result = await _compute(db, student_id, root_course_id)
    cache[(student_id, root_course_id)] = result
    return result


async def _compute(
    db: AsyncSession, student_id: int, root_course_id: int
) -> GracedItems:
    """Расчёт без кеша: три запроса на дерево, дальше чистый Python.

    Запросы — узлы дерева, рёбра между ними и все обязательные элементы дерева
    сразу. Ни один из них не растёт по числу элементов или подкурсов: именно
    рост «запрос на узел» был первопричиной заторов движка (tsk-662), и
    повторять его здесь нельзя.
    """
    node_rows = (
        await db.execute(text(_TREE_NODES_SQL), {"root_id": root_course_id})
    ).fetchall()
    tree_ids = [int(r[0]) for r in node_rows]
    if not tree_ids:
        return EMPTY_GRACE

    edge_rows = (
        await db.execute(text(_TREE_EDGES_SQL), {"root_id": root_course_id})
    ).fetchall()

    item_rows = (
        await db.execute(
            text(_TREE_ITEMS_SQL),
            {
                "student_id": student_id,
                "tree_ids": tree_ids,
                "levels": list(_REQUIRED_LEVELS),
                "pass_threshold": _PASS_THRESHOLD_RATIO,
            },
        )
    ).fetchall()
    if not item_rows:
        return EMPTY_GRACE

    nodes: Dict[int, _Node] = {cid: _Node() for cid in tree_ids}
    for parent_id, child_id in edge_rows:
        parent = nodes.get(int(parent_id))
        if parent is not None and int(child_id) in nodes:
            parent.children.append(int(child_id))

    for kind, item_id, course_id, created_at, done, done_at in item_rows:
        node = nodes.get(int(course_id))
        if node is None:
            continue
        node.own.append(
            _Item(
                kind=str(kind),
                item_id=int(item_id),
                course_id=int(course_id),
                created_at=created_at,
                done=bool(done),
                done_at=done_at,
            )
        )

    graced_tasks: Set[int] = set()
    graced_materials: Set[int] = set()
    for course_id in nodes:
        subtree = _subtree_ids(nodes, course_id)
        items = [item for cid in subtree for item in nodes[cid].own]
        for item in _graced_in_node(items):
            if item.kind == "task":
                graced_tasks.add(item.item_id)
            else:
                graced_materials.add(item.item_id)

    if graced_tasks or graced_materials:
        logger.info(
            "tsk-692: ученик %s, дерево курса %s — снята обязательность: "
            "заданий %s, материалов %s",
            student_id,
            root_course_id,
            len(graced_tasks),
            len(graced_materials),
        )
    return GracedItems(
        tasks=frozenset(graced_tasks), materials=frozenset(graced_materials)
    )


def _subtree_ids(nodes: Dict[int, _Node], course_id: int) -> Set[int]:
    """ID узлов поддерева: сам курс и все его потомки.

    Обход итеративный, с множеством посещённых: `course_parents` — связь
    многие-ко-многим, один узел висит под несколькими родителями и попадает в
    дерево по строке на каждый путь, а не на узел (tsk-261). Без `visited`
    элементы такого узла учлись бы по нескольку раз, а цикл в иерархии увёл бы
    обход в бесконечность.
    """
    subtree: Set[int] = set()
    stack: List[int] = [course_id]
    while stack:
        current = stack.pop()
        if current in subtree or current not in nodes:
            continue
        subtree.add(current)
        stack.extend(nodes[current].children)
    return subtree


def _graced_in_node(items: Sequence[_Item]) -> Iterable[_Item]:
    """Незакрытые элементы узла, которым правило снимает обязательность.

    Узел прощает, если ученик его когда-то закрыл целиком: время последнего
    зачёта `T` определено, и КАЖДЫЙ незакрытый сейчас элемент появился позже
    `T`. Незакрытый элемент без даты появления (`created_at IS NULL`, задания
    старше tsk-692) считается существовавшим всегда — он не прощается сам и
    отменяет прощение всего узла.

    Зачёт без отметки времени тоже отменяет прощение: «закрыл, но неизвестно
    когда» — это отсутствие ответа на главный вопрос правила, а не разрешение
    сдвинуть границу в прошлое.
    """
    done = [i for i in items if i.done]
    if not done:
        # Ученик в этом узле не закрыл ничего — он его и не проходил.
        return ()
    if any(i.done_at is None for i in done):
        return ()

    undone = [i for i in items if not i.done]
    if not undone:
        return ()

    t_cut = max(i.done_at for i in done if i.done_at is not None)
    for item in undone:
        if item.created_at is None or item.created_at <= t_cut:
            # Есть незакрытый элемент, существовавший к моменту последнего
            # зачёта, — значит узел не был пройден. Не прощаем ничего.
            return ()
    return tuple(undone)
