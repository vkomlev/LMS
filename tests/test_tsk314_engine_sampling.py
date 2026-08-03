"""tsk-314: движок выборки заданий по сложности на подкурс (части 2/3).

Часть 1 (вынос HARD в опциональный подкурс) закрыта отдельно в tsk-347 —
здесь не проверяется. Четыре сценария из декомпозиции задачи:
  1. THEORY выдаются все, независимо от выборки.
  2. Превышение порога -> ровно доля с нужным соотношением EASY:NORMAL.
  3. Повторный заход даёт тот же набор (стабильность за учеником).
  4. Выключенный параметр (или конфиг вовсе не задан) = старое поведение.
Плюс регрессия на `compute_course_state`: денаминатор total_tasks обязан
учитывать выборку, иначе подкурс с включённой выборкой никогда не дойдёт
до COMPLETED (см. правку в learning_engine_service.compute_course_state).
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services.learning_engine_service import LearningEngineService


async def _new_course(db, title: str) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": title},
    )
    return int(res.scalar_one())


async def _set_sampling_config(db, course_id: int, config: dict | None) -> None:
    await db.execute(
        text("UPDATE courses SET sampling_config = CAST(:cfg AS jsonb) WHERE id = :id"),
        {"cfg": json.dumps(config) if config is not None else None, "id": course_id},
    )


async def _new_task(db, *, course_id: int, difficulty_id: int, uid: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk314"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": uid,
        },
    )
    return int(res.scalar_one())


async def _new_student(db, *, prefix: str) -> int:
    res = await db.execute(
        text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
        {"n": f"{prefix} tsk314-student"},
    )
    return int(res.scalar_one())


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": user_id, "c": course_id},
    )


async def _complete_task(db, *, user_id: int, task_id: int, course_id: int, root_course_id: int) -> None:
    """Успешное прохождение задания — как реальный сабмит верного ответа."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :r, 'test_tsk314') RETURNING id"
            ),
            {"u": user_id, "c": course_id, "r": root_course_id},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO task_results "
            "(user_id, task_id, attempt_id, score, max_score, is_correct, submitted_at) "
            "VALUES (:u, :t, :a, 10, 10, true, now())"
        ),
        {"u": user_id, "t": task_id, "a": attempt_id},
    )


@pytest_asyncio.fixture
async def difficulty_ids(db):
    rows = (await db.execute(text("SELECT id, code FROM difficulties"))).fetchall()
    by_code = {code: int(did) for did, code in rows}
    for code in ("THEORY", "EASY", "NORMAL"):
        if code not in by_code:
            pytest.skip(f"Нет difficulty с кодом {code} — граф не собрать")
    return by_code


@pytest_asyncio.fixture
async def sampling_course(db, difficulty_ids):
    """Подкурс: 2 THEORY + 6 EASY + 6 NORMAL, выборка включена (threshold=4, 50/50).

    threshold=4 < 12 (EASY+NORMAL) -> выборка срабатывает, итог = 2 EASY + 2
    NORMAL + 2 THEORY (THEORY выборке не подлежит) = 6 заданий на обход.
    """
    course_id = await _new_course(db, "tsk314 sampling subcourse")
    await _set_sampling_config(
        db, course_id, {"enabled": True, "threshold": 4, "easy_ratio": 0.5}
    )

    theory_ids = [
        await _new_task(
            db, course_id=course_id, difficulty_id=difficulty_ids["THEORY"],
            uid=f"tsk314-theory-{i}-{uuid4().hex[:8]}",
        )
        for i in range(2)
    ]
    easy_ids = [
        await _new_task(
            db, course_id=course_id, difficulty_id=difficulty_ids["EASY"],
            uid=f"tsk314-easy-{i}-{uuid4().hex[:8]}",
        )
        for i in range(6)
    ]
    normal_ids = [
        await _new_task(
            db, course_id=course_id, difficulty_id=difficulty_ids["NORMAL"],
            uid=f"tsk314-normal-{i}-{uuid4().hex[:8]}",
        )
        for i in range(6)
    ]
    await db.commit()
    return {
        "course_id": course_id,
        "theory": theory_ids,
        "easy": easy_ids,
        "normal": normal_ids,
    }


@pytest.mark.asyncio
async def test_theory_always_included(db, sampling_course):
    """Сценарий 1: THEORY выдаются все, независимо от выборки EASY/NORMAL."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="theory")

    rows = await engine._effective_task_rows(db, sampling_course["course_id"], student)
    effective_ids = {i for i, _ in rows}

    assert set(sampling_course["theory"]).issubset(effective_ids), (
        "THEORY-задания обязаны выдаваться все, выборка их не касается"
    )


@pytest.mark.asyncio
async def test_threshold_exceeded_gives_share_with_ratio(db, sampling_course):
    """Сценарий 2: превышение порога -> ровно threshold заданий, доля EASY:NORMAL верна."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="ratio")

    rows = await engine._effective_task_rows(db, sampling_course["course_id"], student)
    effective_ids = {i for i, _ in rows}

    easy_kept = effective_ids & set(sampling_course["easy"])
    normal_kept = effective_ids & set(sampling_course["normal"])

    assert len(easy_kept) + len(normal_kept) == 4, (
        "threshold=4 -> ровно 4 EASY+NORMAL задания в выборке, получено "
        f"{len(easy_kept) + len(normal_kept)}"
    )
    assert len(easy_kept) == 2 and len(normal_kept) == 2, (
        f"easy_ratio=0.5 при threshold=4 -> поровну (2/2), получено "
        f"easy={len(easy_kept)} normal={len(normal_kept)}"
    )


@pytest.mark.asyncio
async def test_repeat_visit_gives_same_set(db, sampling_course):
    """Сценарий 3: повторный заход того же студента -> тот же набор (стабильность)."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="stable")

    first = {i for i, _ in await engine._effective_task_rows(db, sampling_course["course_id"], student)}
    second = {i for i, _ in await engine._effective_task_rows(db, sampling_course["course_id"], student)}

    assert first == second, "Повторный вызов для того же студента обязан вернуть тот же набор"


@pytest.mark.asyncio
async def test_disabled_sampling_behaves_like_before(db, sampling_course):
    """Сценарий 4: enabled=false -> прежнее поведение (все задания, без урезания)."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="disabled")

    await _set_sampling_config(
        db, sampling_course["course_id"],
        {"enabled": False, "threshold": 4, "easy_ratio": 0.5},
    )
    await db.commit()

    rows = await engine._effective_task_rows(db, sampling_course["course_id"], student)
    effective_ids = {i for i, _ in rows}
    all_ids = set(sampling_course["theory"]) | set(sampling_course["easy"]) | set(sampling_course["normal"])

    assert effective_ids == all_ids, "enabled=false обязан отдавать все задания без урезания"


@pytest.mark.asyncio
async def test_missing_config_behaves_like_before(db, sampling_course):
    """Сценарий 4 (вариант): sampling_config=NULL -> тоже прежнее поведение."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="nullcfg")

    await _set_sampling_config(db, sampling_course["course_id"], None)
    await db.commit()

    rows = await engine._effective_task_rows(db, sampling_course["course_id"], student)
    effective_ids = {i for i, _ in rows}
    all_ids = set(sampling_course["theory"]) | set(sampling_course["easy"]) | set(sampling_course["normal"])

    assert effective_ids == all_ids


@pytest.mark.asyncio
async def test_resolve_next_item_only_offers_effective_set_and_reaches_completed(
    db, sampling_course
):
    """End-to-end: обход resolve_next_item предлагает ровно эффективный набор
    (никогда не вырезанное выборкой задание), а прохождение только его
    доводит подкурс до COMPLETED (регрессия denominator в compute_course_state:
    без правки total_tasks считал бы и вырезанные задания, и курс завис бы
    в IN_PROGRESS навсегда, хотя студент решил всё, что ему предложили)."""
    engine = LearningEngineService()
    student = await _new_student(db, prefix="e2e")
    course_id = sampling_course["course_id"]
    await _enroll(db, student, course_id)
    await db.commit()

    effective_ids = {
        i for i, _ in await engine._effective_task_rows(db, course_id, student)
    }
    assert len(effective_ids) == 6  # 2 THEORY + 2 EASY + 2 NORMAL

    offered: set[int] = set()
    after_task_id = None
    for _ in range(len(effective_ids) + 2):  # +2 запас, чтобы поймать зацикливание
        result = await engine.resolve_next_item(
            db, student, root_course_id=course_id, after_task_id=after_task_id
        )
        if result.type == "none":
            break
        assert result.type == "task", result
        assert result.task_id in effective_ids, (
            f"resolve_next_item предложил задание {result.task_id}, "
            f"вырезанное выборкой (не входит в {effective_ids})"
        )
        offered.add(result.task_id)
        await _complete_task(
            db, user_id=student, task_id=result.task_id,
            course_id=course_id, root_course_id=course_id,
        )
        await db.commit()
        after_task_id = result.task_id

    assert offered == effective_ids, (
        "Обход обязан предложить РОВНО эффективный набор, не больше и не меньше: "
        f"предложено={offered}, ожидалось={effective_ids}"
    )

    state = await engine.compute_course_state(db, student, course_id)
    assert state.state == "COMPLETED", (
        "Подкурс с выборкой обязан доходить до COMPLETED после решения всех "
        f"предложенных заданий, получено {state.state}"
    )
