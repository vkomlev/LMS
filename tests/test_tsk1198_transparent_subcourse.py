"""tsk-1198: прозрачный подкурс — в курсе-хозяине его задания идут общим списком.

Граф как у подборок практикумов (tsk-1161/1163):

    bank (корень, курс банка «Задание N»)
      ├─ T1, T2, T3            — свои задания, позиции 1..3
      └─ sub (прозрачен для bank): X1, X2
    prak (корень, «Практикум»)
      └─ sub (обычный раздел)

X1 стоял до переноса между T1 и T2 (host_order_position=2), X2 — после T3
(host_order_position=4). В bank порядок обязан быть T1, X1, T2, T3, X2 — во
всех трёх обходах (движок, сводка прогресса, оглавление SPW), без раздела sub
и без двойного счёта; в prak sub остаётся разделом со своим порядком X1, X2.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services import manual_progress_service, me_service
from app.services.learning_engine_service import LearningEngineService
from app.services.tasks_service import TasksService


async def _new_course(db, title: str) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": title},
    )
    return int(res.scalar_one())


async def _new_task(db, *, course_id: int, difficulty_id: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "stem": "tsk1198"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": f"tsk1198-{uuid4().hex[:12]}",
        },
    )
    return int(res.scalar_one())


async def _new_student(db, course_id: int) -> int:
    uid = int(
        (
            await db.execute(
                text("INSERT INTO users (full_name) VALUES ('tsk1198 student') RETURNING id")
            )
        ).scalar_one()
    )
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": uid, "c": course_id},
    )
    return uid


@pytest_asyncio.fixture
async def graph(db):
    """bank + прозрачная подборка sub + практикум prak (см. docstring модуля)."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    if difficulty_id is None:
        pytest.skip("Нет ни одной difficulty — граф не собрать")

    ids: dict[str, int] = {
        "bank": await _new_course(db, "tsk1198 bank"),
        "sub": await _new_course(db, "tsk1198 sub"),
        "prak": await _new_course(db, "tsk1198 prak"),
    }
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, is_transparent) "
            "VALUES (:s, :b, true), (:s, :p, false)"
        ),
        {"s": ids["sub"], "b": ids["bank"], "p": ids["prak"]},
    )
    for name in ("T1", "T2", "T3"):
        ids[name] = await _new_task(db, course_id=ids["bank"], difficulty_id=difficulty_id)
    for name, host_pos in (("X1", 200), ("X2", 400)):
        ids[name] = await _new_task(db, course_id=ids["sub"], difficulty_id=difficulty_id)
        await db.execute(
            text("UPDATE tasks SET host_order_position = :p WHERE id = :t"),
            {"p": host_pos, "t": ids[name]},
        )
    await db.commit()
    return ids


def _order(ids: dict[str, int], names: str) -> list[int]:
    return [ids[n] for n in names.split()]


@pytest.mark.asyncio
async def test_syllabus_bank_is_one_list_practicum_keeps_section(db, graph):
    student = await _new_student(db, graph["bank"])
    await db.commit()

    bank = await me_service.get_syllabus_states(db, user_id=student, root_course_id=graph["bank"])
    assert [s["course_id"] for s in bank["sections"]] == [graph["bank"]]
    tasks = [i for i in bank["items"] if i["kind"] == "task"]
    assert [i["task_id"] for i in tasks] == _order(graph, "T1 X1 T2 T3 X2")
    assert {i["course_id"] for i in tasks} == {graph["bank"]}

    prak = await me_service.get_syllabus_states(db, user_id=student, root_course_id=graph["prak"])
    assert graph["sub"] in [s["course_id"] for s in prak["sections"]]
    sub_tasks = [i["task_id"] for i in prak["items"] if i["course_id"] == graph["sub"]]
    assert sub_tasks == _order(graph, "X1 X2")


@pytest.mark.asyncio
async def test_next_item_walks_bank_in_restored_order(db, graph):
    student = await _new_student(db, graph["bank"])
    await db.commit()
    engine = LearningEngineService()

    first = await engine.resolve_next_item(db, student, root_course_id=graph["bank"])
    assert first.task_id == graph["T1"]
    walk = [first.task_id]
    for _ in range(4):
        nxt = await engine.resolve_next_item(
            db, student, root_course_id=graph["bank"], after_task_id=walk[-1]
        )
        walk.append(nxt.task_id)
    assert walk == _order(graph, "T1 X1 T2 T3 X2")


@pytest.mark.asyncio
async def test_next_item_in_practicum_keeps_subcourse_order(db, graph):
    student = await _new_student(db, graph["prak"])
    await db.commit()
    engine = LearningEngineService()

    first = await engine.resolve_next_item(db, student, root_course_id=graph["prak"])
    assert (first.course_id, first.task_id) == (graph["sub"], graph["X1"])
    nxt = await engine.resolve_next_item(
        db, student, root_course_id=graph["prak"], after_task_id=graph["X1"]
    )
    assert nxt.task_id == graph["X2"]


@pytest.mark.asyncio
async def test_progress_has_no_subcourse_node_and_no_double_count(db, graph):
    student = await _new_student(db, graph["bank"])
    await db.commit()

    progress = await manual_progress_service.get_student_progress(
        db, student_id=student, course_id=graph["bank"]
    )
    nodes = [i["item_id"] for i in progress["items"] if i["item_type"] == "course"]
    assert nodes == [graph["bank"]]
    tasks = [i["item_id"] for i in progress["items"] if i["item_type"] == "task"]
    assert tasks == _order(graph, "T1 X1 T2 T3 X2")

    # Пять заданий — ровно по разу: подборка не задвоилась ни узлом, ни списком.
    assert len(tasks) == len(set(tasks)) == 5


@pytest.mark.asyncio
async def test_tasks_by_course_with_transparent(db, graph):
    service = TasksService()
    plain, plain_total = await service.get_by_course(db, course_id=graph["bank"])
    assert [t.id for t in plain] == _order(graph, "T1 T2 T3") and plain_total == 3

    merged, total = await service.get_by_course(
        db, course_id=graph["bank"], with_transparent=True
    )
    assert [t.id for t in merged] == _order(graph, "T1 X1 T2 T3 X2") and total == 5


@pytest.mark.asyncio
async def test_same_slot_ordered_by_rank_not_id(db, graph):
    """Несколько заданий подборки перед одним заданием хозяина — по рангу (прежний
    порядок), а не по id: X2 (ранг 0) раньше X1 (ранг 1), хотя id у X1 меньше."""
    await db.execute(
        text("UPDATE tasks SET host_order_position = CASE id WHEN :x1 THEN 401 ELSE 400 END "
             "WHERE id IN (:x1, :x2)"),
        {"x1": graph["X1"], "x2": graph["X2"]},
    )
    await db.commit()
    merged, _ = await TasksService().get_by_course(
        db, course_id=graph["bank"], with_transparent=True
    )
    assert [t.id for t in merged] == _order(graph, "T1 T2 T3 X2 X1")
