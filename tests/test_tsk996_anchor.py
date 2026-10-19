"""tsk-996 (фаза B): якорь задания к материалу — порядок узла и инварианты.

ADR-0008, постановка `docs/briefs/2026-10-19-p7-anchor-implementation.md`,
шаги 5–10. Фаза A (`tests/test_tsk996_node_sequence.py`) доказывает, что без
якорей выдача прежняя; здесь — что с якорями она та, что задумана:

* задание с якорем идёт сразу после своего материала (голова узла), внутри
  группы — по `task_key`; без якоря — в конце узла, после подразделов;
* полная цепочка next-item по узлу выдаёт каждый элемент РОВНО ОДИН РАЗ в
  ожидаемом порядке, и next-item от любого элемента как позиции даёт
  следующий за ним — курсор режет смешанную последовательность верно;
* тот же порядок — в программе курса, предпросмотре, карточке прогресса и
  правиле «ушёл вперёд» (все шесть мест берут его из `node_sequence`);
* удаление материала, переезд задания и материала, неактивный материал,
  прозрачный подкурс рядом с якорями, выборка tsk-314, пустая группа;
* «N из M» от перестановки не меняется;
* страж базы не даёт поставить якорь на материал другого курса; API —
  422, `manual`, явный null, «поле не пришло — якорь не трогаем»;
  bulk-upsert якорь не трогает;
* при `NODE_SEQUENCE_ENABLED=false` якоря игнорируются — откат рубильником
  возвращает прежний порядок.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Any, Optional
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.models.users import Users
from app.services import manual_progress_service, me_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.content_grace_service import compute_graced_items
from app.services.learning_engine_service import LearningEngineService
from app.services.preview_navigation import preview_next_item
from app.services.requirement_override_service import set_human_override
from tests.test_tsk996_node_sequence import (
    INSERTED,
    LONG_AGO,
    _chain,
    _complete_material,
    _course,
    _link,
    _material,
    _reset_caches,
    _result,
    _student,
    _task,
)

_settings = Settings()

Item = tuple[str, int]


# ─── Помощники ──────────────────────────────────────────────────────────────


async def _anchor(db, task_id: int, material_id: Optional[int], source: str = "manual") -> None:
    await db.execute(
        text(
            "UPDATE tasks SET anchor_material_id = :m, anchor_source = :s WHERE id = :t"
        ),
        {"m": material_id, "s": source, "t": task_id},
    )


def M(i: int) -> Item:  # noqa: N802 — короткая запись ожиданий
    return ("material", i)


def T(i: int) -> Item:  # noqa: N802
    return ("task", i)


def _items_of_chain(chain: list[tuple]) -> list[Item]:
    out: list[Item] = []
    for step in chain:
        if step[0] == "material":
            out.append(M(step[3]))
        elif step[0] in ("task", "blocked_limit"):
            out.append(T(step[4]))
    return out


async def _chain_items(db, student: int, root: Optional[int]) -> list[Item]:
    _reset_caches(db)
    chain = await _chain(db, student, root)
    assert not any(step[0] == "ЦИКЛ" for step in chain), chain
    return _items_of_chain(chain)


async def _next_after(db, student: int, root: int, item: Item) -> Optional[Item]:
    _reset_caches(db)
    res = await LearningEngineService().resolve_next_item(
        db, student, root_course_id=root,
        after_material_id=item[1] if item[0] == "material" else None,
        after_task_id=item[1] if item[0] == "task" else None,
    )
    if res.type == "material":
        return M(res.material_id)
    if res.type in ("task", "blocked_limit"):
        return T(res.task_id)
    return None


async def _syllabus_section(db, student: int, root: int, course_id: int) -> list[Item]:
    _reset_caches(db)
    data = await me_service.get_syllabus_states(db, user_id=student, root_course_id=root)
    return [
        M(i["material_id"]) if i["kind"] == "material" else T(i["task_id"])
        for i in data["items"]
        if i["course_id"] == course_id
    ]


async def _progress_rows(db, student: int, root: int) -> list[tuple[str, int]]:
    _reset_caches(db)
    data = await manual_progress_service.get_student_progress(
        db, student_id=student, course_id=root
    )
    return [(r["item_type"], r["item_id"]) for r in data["items"]]


async def _preview_chain(db, root: int, first: Item) -> list[Item]:
    _reset_caches(db)
    out = [first]
    seen = {first}
    current = first
    while True:
        found = await preview_next_item(db, current[0], current[1], root_course_id=root)
        if found is None or found.next_kind is None:
            return out
        current = (found.next_kind, int(found.next_id))
        assert current not in seen, f"предпросмотр зациклился на {current}: {out}"
        seen.add(current)
        out.append(current)


def _assert_once(chain: list[Item], expected: list[Item]) -> None:
    assert len(chain) == len(set(chain)), f"повтор в цепочке: {chain}"
    assert chain == expected, f"\nполучено: {chain}\nожидалось: {expected}"


# ─── Основная форма: узел с якорями ─────────────────────────────────────────


async def shape_anchored(db) -> dict[str, Any]:
    """Корень: m1 m2 m3 (+ подраздел c), задания с якорями и без.

    t1(п1)→m1, t2(п2)→m1 (вставлены не по порядку позиций), t4→m3,
    t3, t5 — без якоря. Подраздел c: cm, ct.

    Ожидаемый порядок: m1 t1 t2 m2 m3 t4 | cm ct | t3 t5.
    """
    root = await _course(db, "якоря корень")
    c = await _course(db, "якоря подраздел")
    await _link(db, root, c, 1)
    m1 = await _material(db, root, 1)
    m2 = await _material(db, root, 2)
    m3 = await _material(db, root, 3)
    t2 = await _task(db, root, 1)
    t1 = await _task(db, root, 1)  # вставка на 1 сдвигает t2 на 2
    t3 = await _task(db, root, 3)
    t4 = await _task(db, root, 4)
    t5 = await _task(db, root, 5)
    cm = await _material(db, c, 1)
    ct = await _task(db, c, 1)
    await _anchor(db, t2, m1)
    await _anchor(db, t1, m1)
    await _anchor(db, t4, m3, source="publish")
    student = await _student(db, root)
    return dict(
        root=root, c=c, m1=m1, m2=m2, m3=m3, t1=t1, t2=t2, t3=t3, t4=t4, t5=t5,
        cm=cm, ct=ct, student=student,
    )


def _expected_anchored(s: dict[str, Any]) -> list[Item]:
    return [
        M(s["m1"]), T(s["t1"]), T(s["t2"]), M(s["m2"]), M(s["m3"]), T(s["t4"]),
        M(s["cm"]), T(s["ct"]),
        T(s["t3"]), T(s["t5"]),
    ]


@pytest.mark.asyncio
async def test_chain_each_item_once_in_anchor_order(db, monkeypatch):
    """Цепочка next-item: каждый элемент ровно один раз, якорные — за своим материалом."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    await db.commit()
    expected = _expected_anchored(s)

    _assert_once(await _chain_items(db, s["student"], s["root"]), expected)
    # Без корня — тот же обход единственного зачисления.
    _assert_once(await _chain_items(db, s["student"], None), expected)


@pytest.mark.asyncio
async def test_cursor_from_every_item(db, monkeypatch):
    """next-item от каждого элемента как позиции — следующий за ним по порядку."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    await db.commit()
    expected = _expected_anchored(s)
    for pos, nxt in zip(expected, [*expected[1:], None]):
        assert await _next_after(db, s["student"], s["root"], pos) == nxt, pos


@pytest.mark.asyncio
async def test_all_places_follow_anchor_order(db, monkeypatch):
    """Программа курса, карточка прогресса и предпросмотр — тот же порядок."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    await db.commit()
    expected = _expected_anchored(s)

    assert await _syllabus_section(db, s["student"], s["root"], s["root"]) == [
        M(s["m1"]), T(s["t1"]), T(s["t2"]), M(s["m2"]), M(s["m3"]), T(s["t4"]),
        T(s["t3"]), T(s["t5"]),
    ]
    assert await _syllabus_section(db, s["student"], s["root"], s["c"]) == [
        M(s["cm"]), T(s["ct"]),
    ]
    assert await _progress_rows(db, s["student"], s["root"]) == [
        ("course", s["root"]),
        ("material", s["m1"]), ("task", s["t1"]), ("task", s["t2"]),
        ("material", s["m2"]), ("material", s["m3"]), ("task", s["t4"]),
        ("course", s["c"]), ("material", s["cm"]), ("task", s["ct"]),
        ("task", s["t3"]), ("task", s["t5"]),
    ]
    assert await _preview_chain(db, s["root"], expected[0]) == expected


@pytest.mark.asyncio
async def test_switch_off_ignores_anchors(db, monkeypatch):
    """NODE_SEQUENCE_ENABLED=false — прежний порядок: якоря не читаются (свойство отката)."""
    s = await shape_anchored(db)
    await db.commit()
    legacy = [
        M(s["m1"]), M(s["m2"]), M(s["m3"]),
        M(s["cm"]), T(s["ct"]),
        T(s["t1"]), T(s["t2"]), T(s["t3"]), T(s["t4"]), T(s["t5"]),
    ]
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "false")
    _assert_once(await _chain_items(db, s["student"], s["root"]), legacy)
    assert await _syllabus_section(db, s["student"], s["root"], s["root"]) == [
        M(s["m1"]), M(s["m2"]), M(s["m3"]),
        T(s["t1"]), T(s["t2"]), T(s["t3"]), T(s["t4"]), T(s["t5"]),
    ]
    assert await _preview_chain(db, s["root"], legacy[0]) == legacy
    # Включили обратно — снова порядок якорей.
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    _assert_once(
        await _chain_items(db, s["student"], s["root"]), _expected_anchored(s)
    )


@pytest.mark.asyncio
async def test_progress_counts_do_not_depend_on_anchors(db, monkeypatch):
    """«N из M» и состояние курса — те же до и после перестановки якорями."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    student = s["student"]
    await _complete_material(db, student, s["m1"])
    await _result(db, student, s["t4"], s["root"], s["root"])
    await _result(db, student, s["ct"], s["c"], s["root"])
    await db.commit()
    engine = LearningEngineService()

    async def snapshot() -> tuple:
        _reset_caches(db)
        state = await engine.compute_course_state(db, student, s["root"])
        syl = await me_service.get_syllabus_states(db, user_id=student, root_course_id=s["root"])
        statuses = sorted(
            (i["kind"], i.get("material_id") or i.get("task_id"), i["status"])
            for i in syl["items"]
        )
        courses = [
            c for c in await me_service.get_courses_with_progress(db, student)
            if c.get("course_id", c.get("id")) == s["root"]
        ]
        return state.state, statuses, courses

    with_anchors = await snapshot()
    await db.execute(
        text("UPDATE tasks SET anchor_material_id = NULL, anchor_source = NULL "
             "WHERE course_id = :r"),
        {"r": s["root"]},
    )
    await db.commit()
    assert await snapshot() == with_anchors


# ─── Удаление и переезды ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_material_delete_moves_its_tasks_to_tail(db, monkeypatch):
    """Удалённый материал: якорь NULL (FK SET NULL), задания — в конце узла, не теряются."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    await db.execute(text("DELETE FROM materials WHERE id = :m"), {"m": s["m1"]})
    await db.commit()
    anchors = dict(
        (
            await db.execute(
                text("SELECT id, (anchor_material_id, anchor_source)::text FROM tasks "
                     "WHERE id = ANY(:ids)"),
                {"ids": [s["t1"], s["t2"]]},
            )
        ).fetchall()
    )
    # Источник обнулён тоже: «manual + NULL» значило бы выбор методиста.
    assert anchors == {s["t1"]: "(,)", s["t2"]: "(,)"}
    _assert_once(
        await _chain_items(db, s["student"], s["root"]),
        [
            M(s["m2"]), M(s["m3"]), T(s["t4"]),
            M(s["cm"]), T(s["ct"]),
            T(s["t1"]), T(s["t2"]), T(s["t3"]), T(s["t5"]),
        ],
    )


@pytest.mark.asyncio
async def test_course_delete_with_anchors(db):
    """Удаление курса каскадом (материалы и задания с якорями) проходит без ошибок."""
    s = await shape_anchored(db)
    await db.commit()
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id = :r"),
                     {"r": s["root"]})
    await db.execute(text("DELETE FROM user_courses WHERE course_id = :r"), {"r": s["root"]})
    await db.execute(text("DELETE FROM courses WHERE id = :r"), {"r": s["root"]})
    left = (
        await db.execute(text("SELECT count(*) FROM tasks WHERE course_id = :r"), {"r": s["root"]})
    ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_task_move_clears_anchor(db, monkeypatch):
    """Переезд задания в другой курс (tsk-802) обнуляет якорь и источник."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    await db.execute(
        text("UPDATE tasks SET course_id = :c WHERE id = :t"), {"c": s["c"], "t": s["t1"]}
    )
    await db.commit()
    row = (
        await db.execute(
            text("SELECT course_id, anchor_material_id, anchor_source FROM tasks WHERE id = :t"),
            {"t": s["t1"]},
        )
    ).one()
    assert tuple(row) == (s["c"], None, None)
    # Переехавшее задание — в хвосте подраздела на своей позиции 1 (tsk-802
    # уважает достижимую позицию), то есть перед ct; остальные на местах.
    _assert_once(
        await _chain_items(db, s["student"], s["root"]),
        [
            M(s["m1"]), T(s["t2"]), M(s["m2"]), M(s["m3"]), T(s["t4"]),
            M(s["cm"]), T(s["t1"]), T(s["ct"]),
            T(s["t3"]), T(s["t5"]),
        ],
    )


@pytest.mark.asyncio
async def test_task_move_with_new_anchor_in_same_update(db):
    """Переезд с якорем на материал НОВОГО курса в том же UPDATE — якорь остаётся."""
    s = await shape_anchored(db)
    await db.execute(
        text("UPDATE tasks SET course_id = :c, anchor_material_id = :m WHERE id = :t"),
        {"c": s["c"], "m": s["cm"], "t": s["t1"]},
    )
    row = (
        await db.execute(
            text("SELECT course_id, anchor_material_id FROM tasks WHERE id = :t"), {"t": s["t1"]}
        )
    ).one()
    assert tuple(row) == (s["c"], s["cm"])


@pytest.mark.asyncio
async def test_material_move_clears_anchors_of_old_node(db, monkeypatch):
    """Переезд материала в другой курс снимает якоря заданий старого узла на него."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    from app.repos.materials_repo import MaterialsRepository

    # Тот же путь, что у эндпоинта переноса материала (`move_material`).
    await MaterialsRepository().move_material(db, s["m1"], None, target_course_id=s["c"])
    rows = dict(
        (
            await db.execute(
                text("SELECT id, anchor_source FROM tasks WHERE id = ANY(:ids) "
                     "AND anchor_material_id IS NULL"),
                {"ids": [s["t1"], s["t2"]]},
            )
        ).fetchall()
    )
    assert rows == {s["t1"]: None, s["t2"]: None}
    _assert_once(
        await _chain_items(db, s["student"], s["root"]),
        [
            M(s["m2"]), M(s["m3"]), T(s["t4"]),
            M(s["cm"]), M(s["m1"]), T(s["ct"]),
            T(s["t1"]), T(s["t2"]), T(s["t3"]), T(s["t5"]),
        ],
    )


# ─── Неактивный и «рекомендуемый» материал ──────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_material_keeps_its_tasks_in_place(db, monkeypatch):
    """Выключенный материал не выдаётся, его задания стоят на его месте; включили — вернулся."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    root = await _course(db, "якоря неактивный")
    m1 = await _material(db, root, 1)
    m2 = await _material(db, root, 2, active=False)
    m3 = await _material(db, root, 3, level="recommended")
    m4 = await _material(db, root, 4)
    ta = await _task(db, root, 1)
    tb = await _task(db, root, 2)
    tc = await _task(db, root, 3)
    await _anchor(db, ta, m2)
    await _anchor(db, tb, m3)
    student = await _student(db, root)
    await db.commit()

    expected = [M(m1), T(ta), T(tb), M(m4), T(tc)]
    _assert_once(await _chain_items(db, student, root), expected)
    assert await _syllabus_section(db, student, root, root) == [
        M(m1), T(ta), M(m3), T(tb), M(m4), T(tc),
    ]
    assert await _progress_rows(db, student, root) == [
        ("course", root), ("material", m1), ("task", ta), ("task", tb),
        ("material", m4), ("task", tc),
    ]
    # Позиция на элементах вне обхода — следующий по месту, а не начало узла.
    assert await _next_after(db, student, root, M(m2)) == T(ta)
    assert await _next_after(db, student, root, M(m3)) == T(tb)
    # Предпросмотр: «рекомендуемый» материал виден, но в обход не входит.
    assert (await preview_next_item(db, "material", m3, root_course_id=root)).next_id == tb

    await db.execute(text("UPDATE materials SET is_active = true WHERE id = :m"), {"m": m2})
    await db.commit()
    _assert_once(
        await _chain_items(db, student, root), [M(m1), M(m2), T(ta), T(tb), M(m4), T(tc)]
    )


@pytest.mark.asyncio
async def test_anchor_only_on_inactive_material(db, monkeypatch):
    """Якорь только на выключенный материал — задания не теряются.

    Признак «у узла есть якоря» приходит запросом материалов; он обязан видеть
    и материалы вне обхода, иначе голова не прочтёт задания, а хвост их уже
    не возьмёт. Крайний случай — в узле нет ни одного активного материала.
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    root = await _course(db, "якоря только выключенный")
    m1 = await _material(db, root, 1)
    m2 = await _material(db, root, 2, active=False)
    ta = await _task(db, root, 1)
    tb = await _task(db, root, 2)
    await _anchor(db, tb, m2)
    bare = await _course(db, "якоря без активных материалов")
    gone = await _material(db, bare, 1, active=False)
    tc = await _task(db, bare, 1)
    td = await _task(db, bare, 2)
    await _anchor(db, td, gone)
    student = await _student(db, root, bare)
    await db.commit()

    _assert_once(await _chain_items(db, student, root), [M(m1), T(tb), T(ta)])
    _assert_once(await _chain_items(db, student, bare), [T(td), T(tc)])
    assert await _preview_chain(db, bare, T(td)) == [T(td), T(tc)]
    assert await _syllabus_section(db, student, bare, bare) == [T(td), T(tc)]


# ─── Прозрачный подкурс рядом с якорями ─────────────────────────────────────


@pytest.mark.asyncio
async def test_transparent_subcourse_next_to_anchors(db, monkeypatch):
    """Хозяин с якорями + прозрачная подборка: подборка на своих местах в хвосте.

    Якорь задания подборки на её же материал в списке хозяина не действует
    (tsk-1198 — как прежде), а в практикуме, где подборка — обычный раздел,
    действует.
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    bank = await _course(db, "якоря банк")
    sub = await _course(db, "якоря подборка")
    prak = await _course(db, "якоря практикум")
    await _link(db, bank, sub, 1, transparent=True)
    await _link(db, prak, sub, 1)
    b1 = await _material(db, bank, 1)
    b2 = await _material(db, bank, 2)
    bt1 = await _task(db, bank, 1)
    bt2 = await _task(db, bank, 2)
    bt3 = await _task(db, bank, 3)
    sm = await _material(db, sub, 1)
    x1 = await _task(db, sub, 1, host_position=200)
    x2 = await _task(db, sub, 2, host_position=None)
    await _anchor(db, bt2, b1)
    await _anchor(db, x1, sm)
    student = await _student(db, bank, prak)
    await db.commit()

    # Банк: подборка не раздел; её материал в обходе не участвует (как прежде),
    # x1 — перед bt2 по месту хозяина, но bt2 ушёл в голову к b1.
    _assert_once(
        await _chain_items(db, student, bank),
        [M(b1), T(bt2), M(b2), T(bt1), T(x1), T(bt3), T(x2)],
    )
    # Практикум: подборка — раздел, якорь x1 → sm действует.
    _assert_once(await _chain_items(db, student, prak), [M(sm), T(x1), T(x2)])
    # Программа курса банка: x1 без якоря на своём месте хозяина.
    assert await _syllabus_section(db, student, bank, bank) == [
        M(b1), T(bt2), M(b2), M(sm), T(bt1), T(x1), T(bt3), T(x2),
    ]


# ─── Выборка tsk-314 опустошает группу ──────────────────────────────────────


@pytest.mark.asyncio
async def test_sampling_empties_anchor_group(db, monkeypatch):
    """Задания группы, вырезанные выборкой, не выдаются; пустая группа шагов не даёт."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    topic = await _course(
        db, "якоря выборка",
        sampling='{"enabled": true, "threshold": 3, "easy_ratio": 0.5}',
    )
    m1 = await _material(db, topic, 1)
    m2 = await _material(db, topic, 2)
    easy = [await _task(db, topic, p, difficulty="EASY") for p in range(1, 5)]
    normal = [await _task(db, topic, p, difficulty="NORMAL") for p in range(5, 9)]
    hard = await _task(db, topic, 9, difficulty="HARD")
    student = await _student(db, topic)
    await db.commit()

    engine = LearningEngineService()
    dropped = await engine._sampled_out_task_ids(  # noqa: SLF001
        db, topic, student, {"enabled": True, "threshold": 3, "easy_ratio": 0.5}
    )
    assert dropped, "выборка обязана что-то вырезать — иначе тест ничего не проверяет"
    kept = [t for t in easy + normal if t not in dropped]
    # Вся группа m1 — только вырезанные; за m2 — одно оставшееся и HARD.
    for t in dropped:
        await _anchor(db, t, m1)
    await _anchor(db, kept[0], m2)
    await _anchor(db, hard, m2)
    await db.commit()

    chain = await _chain_items(db, student, topic)
    _assert_once(chain, [M(m1), M(m2), T(kept[0]), T(hard), *[T(t) for t in kept[1:]]])
    assert not set(T(t) for t in dropped) & set(chain)


# ─── Grace и обязательность на смешанной последовательности ─────────────────


async def _shape_grace(db) -> dict[str, Any]:
    """Тема: m1 → t_old, t_new(вставлено позже); m2 → t_b. Ученик давно закрыл m2."""
    root = await _course(db, "якоря grace")
    topic = await _course(db, "якоря grace тема")
    await _link(db, root, topic, 1)
    m1 = await _material(db, topic, 1)
    m2 = await _material(db, topic, 2)
    t_old = await _task(db, topic, 1)
    t_b = await _task(db, topic, 2)
    student = await _student(db, root)
    await _complete_material(db, student, m2, at=LONG_AGO + timedelta(days=3))
    t_new = await _task(db, topic, 3, created_at=INSERTED)
    await _anchor(db, t_old, m1)
    await _anchor(db, t_new, m1)
    await _anchor(db, t_b, m2)
    return dict(root=root, topic=topic, m1=m1, m2=m2, t_old=t_old, t_new=t_new,
                t_b=t_b, student=student)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_overrides", [False, True], ids=["grace-calc", "grace-table"])
async def test_grace_follows_mixed_order(db, monkeypatch, read_overrides):
    """«Ушёл вперёд» (tsk-1261) считается по смешанному порядку.

    t_new стоит ДО m2 только благодаря якорю: m2 закрыт задолго до появления
    t_new — значит t_new позади ученика и долгом не считается. Без якорей
    (рубильник off) t_new стоит после m2 — прощать нечего.
    """
    monkeypatch.setenv(
        "REQUIREMENT_OVERRIDE_READ_ENABLED", "true" if read_overrides else "false"
    )
    s = await _shape_grace(db)
    await db.commit()

    async def graced_tasks() -> set[int]:
        if read_overrides:
            from app.services.requirement_override_service import sync_rule_overrides

            await sync_rule_overrides(db, s["student"])
            await db.commit()
        _reset_caches(db)
        return set((await compute_graced_items(db, s["student"], s["root"])).tasks)

    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    assert s["t_new"] in await graced_tasks()
    # Прощённое — не долг: цепочка его пропускает, остальное — по порядку якорей.
    _assert_once(
        await _chain_items(db, s["student"], s["root"]),
        [M(s["m1"]), T(s["t_old"]), T(s["t_b"])],
    )

    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "false")
    assert s["t_new"] not in await graced_tasks()


@pytest.mark.asyncio
async def test_blocked_limit_on_anchored_task(db, monkeypatch):
    """Исчерпанный лимит у задания в голове с якорями — замок на нём, а не обход мимо."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    s = await shape_anchored(db)
    student = s["student"]
    await _complete_material(db, student, s["m1"])
    await _result(db, student, s["t1"], s["root"], s["root"])
    await _result(db, student, s["t2"], s["root"], s["root"], correct=False, times=3)
    await db.commit()
    _reset_caches(db)
    res = await LearningEngineService().resolve_next_item(db, student, root_course_id=s["root"])
    assert (res.type, res.task_id) == ("blocked_limit", s["t2"]), res


@pytest.mark.asyncio
async def test_personal_requirement_on_anchored_task(db, monkeypatch):
    """Решение учителя «рекомендуемое» (tsk-1361) снимает якорное задание с пути."""
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    # Решения учителя действуют в режиме чтения из таблицы переопределений.
    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
    s = await shape_anchored(db)
    teacher = await _student(db)
    await set_human_override(
        db, student_id=s["student"], target_kind="task", target_id=s["t1"],
        level="recommended", by_user_id=teacher,
    )
    await db.commit()
    expected = [i for i in _expected_anchored(s) if i != T(s["t1"])]
    _assert_once(await _chain_items(db, s["student"], s["root"]), expected)
    # Позиция на снятом с пути задании — следующий по месту (t2), не назад.
    assert await _next_after(db, s["student"], s["root"], T(s["t1"])) == T(s["t2"])


# ─── Число запросов ─────────────────────────────────────────────────────────


async def _count_next_item(db, db_engine, call) -> int:
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    _reset_caches(db)
    event.listen(db_engine.sync_engine, "before_cursor_execute", _record)
    try:
        await call()
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", _record)
    return len(statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [1, 4, 8])
async def test_query_count_does_not_grow_with_groups(db, db_engine, monkeypatch, groups):
    """Пройденный узел с якорями: число запросов next-item не зависит от числа групп.

    Узел: `groups` материалов, за каждым по 2 задания якорем, всё пройдено —
    next-item без позиции проходит голову целиком (худший случай: бот, сводка
    занятия, `/me/last-position`). Голова со смешанными видами проверяется
    одним пакетом; цена якорей — константа (чтение заданий головой), а не
    запросы на каждую группу (класс tsk-655/1250).
    """
    monkeypatch.setenv("NODE_SEQUENCE_ENABLED", "true")
    root = await _course(db, f"якоря замер {groups}")
    student = await _student(db, root)
    tasks: list[tuple[int, int]] = []
    for g in range(1, groups + 1):
        m = await _material(db, root, g)
        await _complete_material(db, student, m)
        for k in range(2):
            t = await _task(db, root, 2 * g - 1 + k)
            await _result(db, student, t, root, root)
            tasks.append((t, m))
    tail = await _task(db, root, 2 * groups + 1)
    await db.commit()
    engine = LearningEngineService()

    async def call():
        res = await engine.resolve_next_item(db, student, root_course_id=root)
        assert (res.type, res.task_id) == ("task", tail), res

    plain = await _count_next_item(db, db_engine, call)
    for t, m in tasks:
        await _anchor(db, t, m)
    await db.commit()
    anchored = await _count_next_item(db, db_engine, call)
    print(f"tsk996 якоря: групп={groups} запросов без якорей={plain} с якорями={anchored}")
    # Голова с якорями: задания узла (+выборка/программа) и пакет из трёх
    # запросов; хвост берёт задания из того же вызова.
    assert anchored <= plain + 6, (groups, plain, anchored)


# ─── Страж базы ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_rejects_anchor_to_other_node(db):
    """Якорь на материал другого курса — ошибка базы (check_violation), и на UPDATE, и на INSERT."""
    s = await shape_anchored(db)
    await db.commit()
    for stmt, params in (
        ("UPDATE tasks SET anchor_material_id = :m WHERE id = :t",
         {"m": s["cm"], "t": s["t3"]}),
        ("INSERT INTO tasks (task_content, course_id, difficulty_id, anchor_material_id) "
         "VALUES (CAST('{\"type\": \"SA\", \"stem\": \"x\"}' AS jsonb), :c, 1, :m)",
         {"c": s["root"], "m": s["cm"]}),
        ("UPDATE tasks SET anchor_material_id = :m WHERE id = :t",
         {"m": 2_000_000_000, "t": s["t3"]}),
    ):
        nested = await db.begin_nested()
        with pytest.raises(DBAPIError) as exc:
            await db.execute(text(stmt), params)
        await nested.rollback()
        assert "check_violation" in repr(exc.value.orig) or "23514" in str(
            getattr(exc.value.orig, "sqlstate", "")
        ) or "якорь" in str(exc.value) or "foreign key" in str(exc.value).lower(), exc.value
    # Свой материал — можно; source вне списка — нельзя.
    await db.execute(
        text("UPDATE tasks SET anchor_material_id = :m WHERE id = :t"), {"m": s["m2"], "t": s["t3"]}
    )
    nested = await db.begin_nested()
    with pytest.raises(DBAPIError):
        await db.execute(
            text("UPDATE tasks SET anchor_source = 'robot' WHERE id = :t"), {"t": s["t3"]}
        )
    await nested.rollback()


@pytest.mark.asyncio
async def test_guard_message_names_task_and_material(db):
    """Отказ стража объясняет, что не так (а не безымянное нарушение)."""
    s = await shape_anchored(db)
    nested = await db.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        await db.execute(
            text("UPDATE tasks SET anchor_material_id = :m WHERE id = :t"),
            {"m": s["cm"], "t": s["t3"]},
        )
    await nested.rollback()
    assert "tsk-996" in str(exc.value) and str(s["cm"]) in str(exc.value)


# ─── API: PATCH /tasks/{id} и bulk-upsert ───────────────────────────────────


async def _user_with_session(db, role: Optional[str]) -> str:
    u = Users(
        email=f"t996-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t996-{role or 'norole'}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return token


async def _anchor_row(db, task_id: int) -> tuple:
    return tuple(
        (
            await db.execute(
                text("SELECT anchor_material_id, anchor_source FROM tasks WHERE id = :t"),
                {"t": task_id},
            )
        ).one()
    )


@pytest.mark.asyncio
async def test_api_patch_anchor(db, client):
    """PATCH: ставит якорь с `manual`, отдаёт его в ответе; 422 на чужой/несуществующий."""
    s = await shape_anchored(db)
    token = await _user_with_session(db, "methodist")
    auth = {"Authorization": f"Bearer {token}"}
    url = f"/api/v1/tasks/{s['t3']}"

    r = await client.patch(url, json={"anchor_material_id": s["m2"]}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anchor_material_id"] == s["m2"] and body["anchor_source"] == "manual"
    assert body["content_provenance"] is None, "якорь — не правка содержимого"
    assert await _anchor_row(db, s["t3"]) == (s["m2"], "manual")

    # Материал другого раздела и несуществующий — 422, якорь прежний.
    for bad in (s["cm"], 2_000_000_000):
        r = await client.patch(url, json={"anchor_material_id": bad}, headers=auth)
        assert r.status_code == 422, r.text
    assert await _anchor_row(db, s["t3"]) == (s["m2"], "manual")

    # Поле не пришло — якорь не трогаем.
    r = await client.patch(url, json={"is_active": True}, headers=auth)
    assert r.status_code == 200, r.text
    assert await _anchor_row(db, s["t3"]) == (s["m2"], "manual")

    # Явный null — «в конце раздела», выбор методиста (manual) сохраняется.
    r = await client.patch(url, json={"anchor_material_id": None}, headers=auth)
    assert r.status_code == 200, r.text
    assert await _anchor_row(db, s["t3"]) == (None, "manual")

    # Перехват якоря публикатора методистом — источник становится manual.
    r = await client.patch(f"/api/v1/tasks/{s['t4']}", json={"anchor_material_id": s["m1"]},
                           headers=auth)
    assert r.status_code == 200, r.text
    assert await _anchor_row(db, s["t4"]) == (s["m1"], "manual")


@pytest.mark.asyncio
async def test_api_patch_anchor_rights(db, client):
    """Права — как у остальных полей задания: ученику и без входа — нельзя."""
    s = await shape_anchored(db)
    url = f"/api/v1/tasks/{s['t3']}"
    r = await client.patch(url, json={"anchor_material_id": s["m2"]})
    assert r.status_code == 401, r.text
    token = await _user_with_session(db, "student")
    r = await client.patch(
        url, json={"anchor_material_id": s["m2"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, r.text
    assert await _anchor_row(db, s["t3"]) == (None, None)


@pytest.mark.asyncio
async def test_bulk_upsert_keeps_anchor(db, client):
    """Переиздание (bulk-upsert) в фазе B якорь не трогает — поле не приходит."""
    s = await shape_anchored(db)
    uid = f"wp:tsk996:{uuid4().hex[:10]}"
    payload = {
        "external_uid": uid,
        "course_id": s["root"],
        "difficulty_id": 2,
        "task_content": {
            "type": "SC", "stem": "якорь",
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        },
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "max_score": 1,
    }
    api_key = next(iter(_settings.valid_api_keys))
    r = await client.post("/api/v1/tasks/bulk-upsert", params={"api_key": api_key},
                          json={"items": [payload]})
    assert r.status_code == 200, r.text
    task_id = int(
        (await db.execute(text("SELECT id FROM tasks WHERE external_uid = :u"), {"u": uid}))
        .scalar_one()
    )
    await _anchor(db, task_id, s["m2"], source="manual")
    await db.commit()

    payload["task_content"]["stem"] = "якорь, переиздание"
    r = await client.post("/api/v1/tasks/bulk-upsert", params={"api_key": api_key},
                          json={"items": [payload]})
    assert r.status_code == 200, r.text
    assert await _anchor_row(db, task_id) == (s["m2"], "manual")
