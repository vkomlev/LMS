"""tsk-545: `resolve_next_item` синхронно освежает кеш ПОДКУРСОВОЙ
зависимости в момент, когда студент проходит узел-пререквизит.

Живая находка оператора (2026-08-03): прошёл тему «Установка Python»
(курс 90 в реальном дереве), следующая тема (курс 106, зависимость
`course_dependencies.course_id=106 required_course_id=90`, ОБА — подкурсы
корня 88, не сам корень) реально открывалась в next-item, но список тем ниже
на странице курса (`me_service.get_syllabus_states` → `_BLOCKED_COURSES_SQL`,
читает `student_course_state`) продолжал показывать её заблокированной, пока
студент не выходил и не заходил в курс заново.

Причина (подтверждено на прод-данных через MCP `learn_prod_db`, см.
`.skill-engaged-note.md`): `resolve_next_item` пересчитывает
`student_course_state` только для зависимостей КОРНЯ
(`list_dependencies(db, current_root_id)`). Зависимость подкурс→подкурс
(курс 106 требует курс 90) этому циклу не видна вовсе — `course_dependencies.
course_id=106 != current_root_id=88`. Единственный путь, который её ловил, —
фоновый тик `course_dependency_state_cron_service` (интервал 15 минут,
tsk-541), отсюда и наблюдаемая задержка.

Тот же граф, что в `test_tsk541_subcourse_dependency_state_backfill.py`:
root → child_a, child_b (оба — дети root через course_parents), по одному
заданию в каждом, зависимость child_b→child_a. Разница с tsk-541: там чинили
путь ЗАПИСИ зависимости (add_dependency/bulk_add_dependencies) и путь записи
в обход API (фоновый тик); здесь — путь ОБЫЧНОГО прохождения (resolve_next_item
уже вызывается на каждый сабмит через /learning/next-item), при уже
существующей зависимости.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.repos.course_dependencies_repository import CourseDependenciesRepository
from app.services.learning_engine_service import LearningEngineService


async def _new_course(db, title: str) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": title},
    )
    return int(res.scalar_one())


async def _new_task(db, *, course_id: int, difficulty_id: int, uid: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk545"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": uid,
        },
    )
    return int(res.scalar_one())


async def _new_student(db, *, prefix: str) -> int:
    res = await db.execute(
        text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
        {"n": f"{prefix} tsk545-student"},
    )
    return int(res.scalar_one())


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true)"
        ),
        {"u": user_id, "c": course_id},
    )


async def _complete_task(
    db, *, user_id: int, task_id: int, course_id: int, root_course_id: int
) -> None:
    """Успешное прохождение задания — как реальный сабмит ответа."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :r, 'test_tsk545') RETURNING id"
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


async def _cache_state(db, *, student_id: int, course_id: int) -> str | None:
    row = (
        await db.execute(
            text(
                "SELECT state FROM student_course_state "
                "WHERE student_id = :s AND course_id = :c"
            ),
            {"s": student_id, "c": course_id},
        )
    ).fetchone()
    return row[0] if row else None


async def _is_blocked(db, *, student_id: int, required_course_id: int) -> bool:
    """Та же формула, что `me_service._BLOCKED_COURSES_SQL`."""
    return bool(
        (
            await db.execute(
                text(
                    "SELECT NOT EXISTS ("
                    "  SELECT 1 FROM student_course_state "
                    "  WHERE student_id = :s AND course_id = :req AND state = 'COMPLETED'"
                    ")"
                ),
                {"s": student_id, "req": required_course_id},
            )
        ).scalar()
    )


@pytest_asyncio.fixture
async def dep_graph(db):
    """root → child_a, child_b (course_parents), одно задание в каждом ребёнке,
    зависимость child_b→child_a записана заранее (как уже существующее
    состояние — тест проверяет путь ЧТЕНИЯ/ПРОХОЖДЕНИЯ, не путь записи)."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    if difficulty_id is None:
        pytest.skip("Нет ни одной difficulty — граф не собрать")

    ids: dict[str, int] = {}
    ids["root"] = await _new_course(db, "tsk545 root")
    ids["child_a"] = await _new_course(db, "tsk545 child A (prerequisite)")
    ids["child_b"] = await _new_course(db, "tsk545 child B (gated)")
    for child in ("child_a", "child_b"):
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id) "
                "VALUES (:c, :p)"
            ),
            {"c": ids[child], "p": ids["root"]},
        )
    ids["task_a"] = await _new_task(
        db, course_id=ids["child_a"], difficulty_id=difficulty_id,
        uid=f"tsk545-a-{uuid4().hex[:12]}",
    )
    ids["task_b"] = await _new_task(
        db, course_id=ids["child_b"], difficulty_id=difficulty_id,
        uid=f"tsk545-b-{uuid4().hex[:12]}",
    )
    # Зависимость подкурс→подкурс — НИ ОДНА сторона не совпадает с root,
    # ровно как реальная пара курс 106 → курс 90 в дереве курса 88.
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:c, :r)"
        ),
        {"c": ids["child_b"], "r": ids["child_a"]},
    )
    await db.commit()
    return ids


@pytest.mark.asyncio
async def test_resolve_next_item_backfills_prerequisite_state_synchronously(db, dep_graph):
    """Регрессия tsk-545: сабмит ответа в узле-пререквизите должен сразу же
    (в рамках того же вызова `resolve_next_item`, без фонового тика)
    обновить `student_course_state`, иначе синтабус показывает следующую
    тему заблокированной, пока next-item уже пускает студента дальше."""
    ids = dep_graph
    student = await _new_student(db, prefix="synced")
    await _enroll(db, student, ids["root"])
    await _complete_task(
        db, user_id=student, task_id=ids["task_a"],
        course_id=ids["child_a"], root_course_id=ids["root"],
    )
    await db.commit()

    # precondition — до вызова next-item кеш пуст (до фикса он и остаётся
    # пустым до фонового тика, что и наблюдал оператор).
    assert await _cache_state(db, student_id=student, course_id=ids["child_a"]) is None

    engine = LearningEngineService()
    await engine.resolve_next_item(db, student, after_task_id=ids["task_a"])

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "COMPLETED", (
        "resolve_next_item не пересчитал student_course_state для подкурсовой "
        f"зависимости синхронно (регрессия tsk-545): state={state!r}"
    )
    assert not await _is_blocked(db, student_id=student, required_course_id=ids["child_a"]), (
        "child_b всё ещё считается заблокированным сразу после прохождения "
        "child_a — список тем должен разблокироваться без выхода из курса"
    )


@pytest.mark.asyncio
async def test_resolve_next_item_does_not_blindly_complete_unfinished_prerequisite(
    db, dep_graph
):
    """Синхронный пересчёт считает РЕАЛЬНОЕ состояние узла, а не слепо
    проставляет COMPLETED при каждом вызове next-item в его контексте."""
    ids = dep_graph
    student = await _new_student(db, prefix="unfinished")
    await _enroll(db, student, ids["root"])
    await db.commit()  # task_a НЕ пройден

    engine = LearningEngineService()
    await engine.resolve_next_item(db, student, after_task_id=ids["task_a"])

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "NOT_STARTED", state
    assert await _is_blocked(db, student_id=student, required_course_id=ids["child_a"])


@pytest.mark.asyncio
async def test_is_required_elsewhere(db, dep_graph):
    """Юнит-проверка нового репозиторного метода: True для узла-пререквизита,
    False для узла, на который никто не ссылается как на required_course_id."""
    ids = dep_graph
    repo = CourseDependenciesRepository()

    assert await repo.is_required_elsewhere(db, ids["child_a"]) is True
    assert await repo.is_required_elsewhere(db, ids["child_b"]) is False
    assert await repo.is_required_elsewhere(db, ids["root"]) is False
