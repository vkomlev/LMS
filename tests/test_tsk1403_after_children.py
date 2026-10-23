"""tsk-1403: материал узла «после подразделов» (`materials.placement`).

Вариант В для ОГЭ: разборы урока узла должны идти после подкурсов-основ, но
сами материалы остаются в узле (CB переиздаёт урок одним документом, якоря
заданий прозрачных подкурсов целы). Материал `after_children` встаёт в начало
хвоста узла, задания с якорем на него — сразу следом, задания без якоря — после.

Дерево: root(intro, after, rt→after, rt2) → [child(cm, ct)].
При рубильнике: intro → cm → ct → after → rt → rt2.
Без рубильника — прежний порядок: intro → after → rt → cm → ct → rt2.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.services import me_service
from app.services.content_grace_service import _Item, _Node, _walk_sequence
from app.services.learning_engine_service import LearningEngineService
from app.services.node_sequence import (
    SEG_TAIL,
    anchor_of,
    in_after_part,
    material_seq_key,
    node_sequence,
    task_seq_key,
)
from app.services.preview_navigation import _ordered_items
from app.services.transparent_subcourse import task_key
from tests.test_tsk261_next_item_position import (
    _course,
    _enroll,
    _link,
    _material,
    _student,
    _task,
)

AFTER = "after_children"


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("NODE_AFTER_CHILDREN_ENABLED", "true")


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv("NODE_AFTER_CHILDREN_ENABLED", raising=False)


# ─── Ключи последовательности (без БД) ──────────────────────────────────────


def _order(materials, tasks):
    return [(e.kind, e.item_id) for e in node_sequence(materials=materials, tasks=tasks)]


def test_flag_off_ignores_placement(flag_off):
    """Без рубильника признак не влияет ни на ключ, ни на порядок."""
    assert material_seq_key(1, 10, 5, AFTER) == material_seq_key(1, 10, 5)
    assert anchor_of(10, 1, 5, anchor_placement=AFTER) == (10, 1, 5)
    order = _order(
        [(10, 1, 5), (11, 2, 5, AFTER)],
        [(20, task_key(1, 20), anchor_of(11, 2, 5, anchor_placement=AFTER)), (21, task_key(2, 21))],
    )
    assert order == [("material", 10), ("material", 11), ("task", 20), ("task", 21)]


def test_after_material_heads_the_tail(flag_on):
    """After-материал — в хвосте перед заданиями без якоря, его задания следом."""
    order = _order(
        [(10, 1, 5), (11, 2, 5, AFTER), (12, 3, 5, AFTER)],
        [
            (21, task_key(1, 21)),
            (20, task_key(2, 20), anchor_of(12, 3, 5, anchor_placement=AFTER)),
            (22, task_key(3, 22), anchor_of(11, 2, 5, anchor_placement=AFTER)),
            (23, task_key(4, 23), anchor_of(10, 1, 5, anchor_placement="head")),
        ],
    )
    assert order == [
        ("material", 10), ("task", 23),
        ("material", 11), ("task", 22),
        ("material", 12), ("task", 20),
        ("task", 21),
    ]
    seq = node_sequence(materials=[(11, 2, 5, AFTER)], tasks=[(21, task_key(1, 21))])
    assert [in_after_part(e) for e in seq] == [True, False]
    assert all(e.key[0] == SEG_TAIL for e in seq)


def test_inactive_after_material_keeps_anchored_place(flag_on):
    """Неактивный after-материал вне выдачи, но его задания стоят на его месте."""
    order = _order(
        [(10, 1, 5)],
        [
            (21, task_key(1, 21)),
            (20, task_key(2, 20), anchor_of(11, 2, 5, anchor_placement=AFTER)),
        ],
    )
    assert order == [("material", 10), ("task", 20), ("task", 21)]
    assert task_seq_key(task_key(2, 20), anchor_of(11, 2, 5, anchor_placement=AFTER))[0] == SEG_TAIL


def test_absorbed_task_anchor_on_own_subcourse_after_material(flag_on):
    """Якорь задания прозрачного подкурса на after-материал самого подкурса в
    списке хозяина не действует — задание в хвосте хозяина без якоря."""
    assert anchor_of(
        11, 2, 7, absorbed=True, anchor_course_id=7, host_course_id=5,
        anchor_placement=AFTER,
    ) is None
    # На after-материал хозяина — действует и уводит в хвост хозяина.
    anchor = anchor_of(
        11, 2, 7, absorbed=True, anchor_course_id=5, host_course_id=5,
        anchor_placement=AFTER,
    )
    assert anchor == (11, 2, 5, AFTER)


def _grace_tree():
    now = datetime.now(timezone.utc)

    def item(kind, iid, cid, op, **kw):
        return _Item(kind=kind, item_id=iid, course_id=cid, order_position=op,
                     created_at=now, done=False, done_at=None, **kw)

    root, child = _Node(), _Node()
    root.children.append(2)
    root.child_order.append((1, 2))
    root.own = [
        item("material", 10, 1, 1, placement="head"),
        item("material", 11, 1, 2, placement=AFTER),
        item("task", 20, 1, 1, anchor_material_id=11, anchor_order_position=2,
             anchor_course_id=1, placement=AFTER),
        item("task", 21, 1, 2),
    ]
    child.own = [item("material", 30, 2, 1, placement="head"), item("task", 40, 2, 1)]
    return {1: root, 2: child}


def test_grace_walk_flag_off_keeps_old_order(flag_off):
    """Без рубильника правило «ушёл вперёд» обходит дерево по-старому."""
    seq = _walk_sequence(_grace_tree(), 1)
    assert [(i.kind, i.item_id) for i in seq] == [
        ("material", 10), ("material", 11), ("task", 20),
        ("material", 30), ("task", 40), ("task", 21),
    ]


def test_grace_walk_follows_after_placement(flag_on):
    """Правило «ушёл вперёд» обходит дерево в том же порядке, что и движок."""
    now = datetime.now(timezone.utc)

    def item(kind, iid, cid, op, **kw):
        return _Item(kind=kind, item_id=iid, course_id=cid, order_position=op,
                     created_at=now, done=False, done_at=None, **kw)

    root, child = _Node(), _Node()
    root.children.append(2)
    root.child_order.append((1, 2))
    root.own = [
        item("material", 10, 1, 1, placement="head"),
        item("material", 11, 1, 2, placement=AFTER),
        item("task", 20, 1, 1, anchor_material_id=11, anchor_order_position=2,
             anchor_course_id=1, placement=AFTER),
        item("task", 21, 1, 2),
    ]
    child.own = [item("material", 30, 2, 1, placement="head"), item("task", 40, 2, 1)]
    seq = _walk_sequence({1: root, 2: child}, 1)
    assert [(i.kind, i.item_id) for i in seq] == [
        ("material", 10), ("material", 30), ("task", 40),
        ("material", 11), ("task", 20), ("task", 21),
    ]


# ─── Движок, программа курса, предпросмотр (БД) ─────────────────────────────


@pytest.fixture
async def tree(db):
    user_id = await _student(db)
    root = await _course(db, "tsk1403 узел")
    child = await _course(db, "tsk1403 основы")
    await _link(db, course_id=child, parent_course_id=root, order_number=1)
    intro = await _material(db, course_id=root, order_position=1, title="вступление")
    after = await _material(db, course_id=root, order_position=2, title="разбор")
    rt = await _task(db, course_id=root, order_position=1)
    rt2 = await _task(db, course_id=root, order_position=2)
    cm = await _material(db, course_id=child, order_position=1, title="теория")
    ct = await _task(db, course_id=child, order_position=1)
    await db.execute(text("UPDATE materials SET placement = 'after_children' WHERE id = :m"), {"m": after})
    await db.execute(
        text("UPDATE tasks SET anchor_material_id = :m, anchor_source = 'manual' WHERE id = :t"),
        {"m": after, "t": rt},
    )
    await _enroll(db, user_id, root)
    await db.commit()
    yield {"user_id": user_id, "root": root, "child": child, "intro": intro,
           "after": after, "rt": rt, "rt2": rt2, "cm": cm, "ct": ct}

    await db.execute(text("DELETE FROM student_material_progress WHERE student_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": [root, child]})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:ids)"), {"ids": [root, child]})
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id=:r"), {"r": root})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": [child, root]})
    await db.commit()


async def _next(db, tree, **pos):
    res = await LearningEngineService().resolve_next_item(
        db, tree["user_id"], root_course_id=tree["root"], **pos
    )
    return (res.type, res.material_id if res.type == "material" else res.task_id)


@pytest.mark.asyncio
async def test_engine_walk_flag_on(db, tree, flag_on):
    """Курсор next-item проходит intro → cm → ct → after → rt → rt2."""
    assert await _next(db, tree, after_material_id=tree["intro"]) == ("material", tree["cm"])
    assert await _next(db, tree, after_task_id=tree["ct"]) == ("material", tree["after"])
    assert await _next(db, tree, after_material_id=tree["after"]) == ("task", tree["rt"])
    assert await _next(db, tree, after_task_id=tree["rt"]) == ("task", tree["rt2"])


@pytest.mark.asyncio
async def test_engine_walk_flag_off(db, tree, flag_off):
    """Без рубильника прежний порядок: разбор и его задание — до подкурса."""
    assert await _next(db, tree, after_material_id=tree["intro"]) == ("material", tree["after"])
    assert await _next(db, tree, after_task_id=tree["rt"]) == ("material", tree["cm"])
    assert await _next(db, tree, after_task_id=tree["ct"]) == ("task", tree["rt2"])


@pytest.mark.asyncio
async def test_preview_order_flag_on(db, tree, flag_on):
    """Предпросмотр идёт тем же порядком, что движок."""
    items = [
        (kind, iid)
        async for kind, iid, _ in _ordered_items(db, LearningEngineService(), tree["root"])
    ]
    assert items == [
        ("material", tree["intro"]), ("material", tree["cm"]), ("task", tree["ct"]),
        ("material", tree["after"]), ("task", tree["rt"]), ("task", tree["rt2"]),
    ]


@pytest.mark.asyncio
async def test_syllabus_after_section(db, tree, flag_on):
    """Программа: элементы части after помечены и стоят в разделе-дубле узла."""
    data = await me_service.get_syllabus_states(
        db, user_id=tree["user_id"], root_course_id=tree["root"]
    )
    sections = [(s["course_id"], s.get("part")) for s in data["sections"]]
    assert sections == [(tree["root"], None), (tree["child"], None), (tree["root"], "after")]
    root_items = [
        (i["kind"], i.get("material_id") or i.get("task_id"), i.get("part"))
        for i in data["items"] if i["course_id"] == tree["root"]
    ]
    assert root_items == [
        ("material", tree["intro"], None), ("task", tree["rt2"], None),
        ("material", tree["after"], "after"), ("task", tree["rt"], "after"),
    ]


@pytest.mark.asyncio
async def test_syllabus_flag_off_has_no_part(db, tree, flag_off):
    """Без рубильника — ни поля part, ни раздела-дубля."""
    data = await me_service.get_syllabus_states(
        db, user_id=tree["user_id"], root_course_id=tree["root"]
    )
    assert all("part" not in s for s in data["sections"])
    assert all("part" not in i for i in data["items"])
    assert len(data["sections"]) == 2


@pytest.mark.asyncio
async def test_node_without_children(db, flag_on):
    """Узел без подкурсов: after-материал всё равно после головы, перед хвостом."""
    user_id = await _student(db)
    root = await _course(db, "tsk1403 лист")
    m1 = await _material(db, course_id=root, order_position=1, title="m1")
    m2 = await _material(db, course_id=root, order_position=2, title="m2")
    t1 = await _task(db, course_id=root, order_position=1)
    await db.execute(text("UPDATE materials SET placement = 'after_children' WHERE id = :m"), {"m": m1})
    await _enroll(db, user_id, root)
    await db.commit()
    try:
        items = [
            (k, i) async for k, i, _ in _ordered_items(db, LearningEngineService(), root)
        ]
        assert items == [("material", m2), ("material", m1), ("task", t1)]
        res = await LearningEngineService().resolve_next_item(
            db, user_id, root_course_id=root, after_material_id=m2
        )
        assert res.type == "material" and res.material_id == m1
    finally:
        await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": user_id})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
        await db.execute(text("DELETE FROM tasks WHERE course_id=:c"), {"c": root})
        await db.execute(text("DELETE FROM materials WHERE course_id=:c"), {"c": root})
        await db.execute(text("DELETE FROM courses WHERE id=:c"), {"c": root})
        await db.commit()


# ─── Запись поля: bulk-upsert ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_upsert_placement_only_when_given(db):
    """Импорт пишет placement только при явной передаче и не перебивает ручную правку."""
    from app.services.materials_service import MaterialsService

    course = await _course(db, "tsk1403 импорт")
    await db.commit()
    svc = MaterialsService()
    base = {
        "course_id": course, "external_uid": "tsk1403:m1", "title": "разбор",
        "type": "text", "content": {"text": "x"},
    }

    async def placement() -> str:
        r = await db.execute(
            text("SELECT placement FROM materials WHERE course_id=:c AND external_uid='tsk1403:m1'"),
            {"c": course},
        )
        return r.scalar_one()

    try:
        await svc.bulk_upsert(db, [dict(base)])
        assert await placement() == "head"
        res = await svc.bulk_upsert(db, [{**base, "placement": AFTER}])
        assert res.items[0].status == "updated"
        assert await placement() == AFTER
        # Переиздание без поля (так шлёт ContentBackbone) — место не трогает.
        res = await svc.bulk_upsert(db, [dict(base)])
        assert res.items[0].status == "unchanged"
        assert await placement() == AFTER
        # Ручная правка места защищена и от явной передачи.
        await db.execute(
            text("UPDATE materials SET content_provenance = CAST(:p AS jsonb) "
                 "WHERE course_id=:c"),
            {"c": course, "p": '{"source": "manual_web", "fields": ["placement"]}'},
        )
        await db.commit()
        res = await svc.bulk_upsert(db, [{**base, "placement": "head"}])
        assert res.items[0].status == "unchanged"
        assert await placement() == AFTER
    finally:
        await db.execute(text("DELETE FROM materials WHERE course_id=:c"), {"c": course})
        await db.execute(text("DELETE FROM courses WHERE id=:c"), {"c": course})
        await db.commit()
