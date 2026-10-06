"""tsk-1250: материалы узла — вступление ДО его подразделов, задания — ПОСЛЕ.

Флагман, трек 2: материал 2620 лежит прямо в разделе 1165, у раздела пять
подразделов. При обходе «сначала подразделы» (tsk-127) next-item вёл ученика во
вступление последним, а экран зачётов показывал его после «Практики». Банк задач
ОГЭ/ЕГЭ (задания узла, теория в подразделах) при этом должен остаться после
теории — поэтому задания узла идут после подразделов.

Дерево: root(intro, rt) → [child(cm, ct)].
Ожидаемый обход: intro → cm → ct → rt.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.learning_engine_service import LearningEngineService
from tests.test_tsk261_next_item_position import (
    _complete_material,
    _course,
    _enroll,
    _link,
    _material,
    _student,
    _task,
)


@pytest.fixture
async def tree(db):
    user_id = await _student(db)
    root = await _course(db, "tsk1250 раздел")
    child = await _course(db, "tsk1250 подраздел")
    await _link(db, course_id=child, parent_course_id=root, order_number=1)
    intro = await _material(db, course_id=root, order_position=1, title="вступление")
    rt = await _task(db, course_id=root, order_position=1)
    cm = await _material(db, course_id=child, order_position=1, title="теория")
    ct = await _task(db, course_id=child, order_position=1)
    await _enroll(db, user_id, root)
    yield {"user_id": user_id, "root": root, "child": child,
           "intro": intro, "rt": rt, "cm": cm, "ct": ct}

    await db.execute(text("DELETE FROM student_material_progress WHERE student_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:ids)"), {"ids": [root, child]})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": [root, child]})
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id=:r"), {"r": root})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": [child, root]})
    await db.commit()


@pytest.mark.asyncio
async def test_steps_order(db, tree):
    """Шаги обхода: материалы узла → подраздел → задания узла."""
    steps = await LearningEngineService()._collect_steps_in_order(db, tree["root"])  # noqa: SLF001
    assert steps == [
        (tree["root"], "materials"),
        (tree["child"], "materials"),
        (tree["child"], "tasks"),
        (tree["root"], "tasks"),
    ]


@pytest.mark.asyncio
async def test_next_item_starts_with_intro(db, tree):
    """С начала курса ученик попадает во вступление раздела, а не в подраздел."""
    res = await LearningEngineService().resolve_next_item(
        db, tree["user_id"], root_course_id=tree["root"]
    )
    assert res.type == "material" and res.material_id == tree["intro"], res


@pytest.mark.asyncio
async def test_after_intro_goes_into_subsection(db, tree):
    """После вступления — в подраздел; задание раздела ещё впереди, не сейчас."""
    await _complete_material(db, student_id=tree["user_id"], material_id=tree["intro"])
    res = await LearningEngineService().resolve_next_item(
        db, tree["user_id"], root_course_id=tree["root"], after_material_id=tree["intro"]
    )
    assert res.type == "material" and res.material_id == tree["cm"], res


@pytest.mark.asyncio
async def test_node_tasks_after_subsections(db, tree):
    """После задания подраздела — задание самого раздела (банк задач после теории)."""
    res = await LearningEngineService().resolve_next_item(
        db, tree["user_id"], root_course_id=tree["root"], after_task_id=tree["ct"]
    )
    assert res.type == "task" and res.task_id == tree["rt"], res
