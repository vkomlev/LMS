"""tsk-541: student_course_state — фоновый пересчёт кеша ПОДКУРСОВ, целей
`course_dependencies`.

Регрессия tsk-523: добавление `course_dependencies` НА ПОДКУРС (не на корень)
молча блокировало синтабус всех активных студентов по нему, даже уже
прошедших пререквизит — `student_course_state` для подкурсов не писал никто
фоново (ни `resolve_next_item`, ни `manual_progress_service.
_refresh_course_state` — оба пишут кеш только КОРНЯ). Обнаружено и закрыто
вручную бэкфиллом 340 строк, не системным фиксом (см.
`reviews/2026-08-02-tsk523-course88-fixes.md`).

Важно: в tsk-523 `course_dependencies` были записаны ПРЯМЫМ SQL-скриптом
(`tsk523_apply.py`) под протоколом `/db-check`, в обход API/сервисного слоя.
Поэтому здесь два независимых теста фикса:
- синхронный бэкфилл в `CourseDependenciesService` (путь записи через API);
- фоновый тик `course_dependency_state_cron_service` (путь записи в обход
  API — ровно тот способ, которым была внесена сама регрессия tsk-523; без
  этого теста фикс выглядел бы полным, а на самом деле не покрывал бы
  реальный инцидент).

Граф: root → child_a, child_b (оба — дети root через course_parents),
по одному заданию в каждом. Студент записан только на root (как в проде —
подкурсы не enroll'ятся напрямую).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.services import course_dependency_state_cron_service
from app.services.course_dependencies_service import CourseDependenciesService


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
            "tc": '{"type": "SA", "question": "tsk541"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": uid,
        },
    )
    return int(res.scalar_one())


async def _new_student(db, *, prefix: str) -> int:
    res = await db.execute(
        text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
        {"n": f"{prefix} tsk541-student"},
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
    """Прошлое прохождение задания (score=max_score) — не через checking_service,
    как реальный прогресс, накопленный ДО того, как на подкурс повесили
    зависимость (ровно ситуация tsk-523: студент 3 уже прошёл курс 103)."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :r, 'test_tsk541') RETURNING id"
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
    """root → child_a, child_b (course_parents), одно задание в каждом ребёнке."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    if difficulty_id is None:
        pytest.skip("Нет ни одной difficulty — граф не собрать")

    ids: dict[str, int] = {}
    ids["root"] = await _new_course(db, "tsk541 root")
    ids["child_a"] = await _new_course(db, "tsk541 child A (prerequisite)")
    ids["child_b"] = await _new_course(db, "tsk541 child B (gated)")
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
        uid=f"tsk541-a-{uuid4().hex[:12]}",
    )
    ids["task_b"] = await _new_task(
        db, course_id=ids["child_b"], difficulty_id=difficulty_id,
        uid=f"tsk541-b-{uuid4().hex[:12]}",
    )
    await db.commit()
    return ids


@pytest.mark.asyncio
async def test_add_dependency_backfills_completed_prerequisite(db, dep_graph):
    """Регрессия tsk-523, путь записи через API/сервис.

    Студент уже прошёл child_a ДО того, как на child_b повесили зависимость
    от него. На коде до фикса `student_course_state` для child_a остаётся
    пустой строкой навсегда → child_b считается заблокированным для студента,
    реально прошедшего пререквизит.
    """
    ids = dep_graph
    student = await _new_student(db, prefix="completed")
    await _enroll(db, student, ids["root"])
    await _complete_task(
        db, user_id=student, task_id=ids["task_a"],
        course_id=ids["child_a"], root_course_id=ids["root"],
    )
    await db.commit()

    # precondition — до записи зависимости кеш действительно пуст (никто его
    # фоново не пишет для подкурса).
    assert await _cache_state(db, student_id=student, course_id=ids["child_a"]) is None

    await CourseDependenciesService().add_dependency(
        db, course_id=ids["child_b"], required_course_id=ids["child_a"]
    )

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "COMPLETED", (
        "student_course_state не пересчитан при записи course_dependencies "
        f"на подкурс (регрессия tsk-523): state={state!r}"
    )
    assert not await _is_blocked(db, student_id=student, required_course_id=ids["child_a"]), (
        "child_b всё ещё считается заблокированным несмотря на пройденный пререквизит"
    )


@pytest.mark.asyncio
async def test_add_dependency_backfill_keeps_incomplete_prerequisite_blocked(db, dep_graph):
    """Бэкфилл считает РЕАЛЬНОЕ состояние, а не слепо проставляет COMPLETED."""
    ids = dep_graph
    student = await _new_student(db, prefix="incomplete")
    await _enroll(db, student, ids["root"])
    await db.commit()  # task_a НЕ пройден

    await CourseDependenciesService().add_dependency(
        db, course_id=ids["child_b"], required_course_id=ids["child_a"]
    )

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "NOT_STARTED", state
    assert await _is_blocked(db, student_id=student, required_course_id=ids["child_a"])


@pytest.mark.asyncio
async def test_bulk_add_dependencies_backfills_state(db, dep_graph):
    """Тот же бэкфилл для массового эндпоинта (реальный write-путь методиста)."""
    ids = dep_graph
    student = await _new_student(db, prefix="bulk")
    await _enroll(db, student, ids["root"])
    await _complete_task(
        db, user_id=student, task_id=ids["task_a"],
        course_id=ids["child_a"], root_course_id=ids["root"],
    )
    await db.commit()

    added = await CourseDependenciesService().bulk_add_dependencies(
        db, ids["child_b"], [ids["child_a"]]
    )
    assert len(added) == 1

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "COMPLETED", state


@pytest.mark.asyncio
async def test_backfill_scoped_to_active_students_of_gated_course(db, dep_graph):
    """Бэкфилл не должен писать кеш студентам, не записанным в дерево child_b."""
    ids = dep_graph
    unrelated = await _new_student(db, prefix="unrelated")
    # НЕ enroll — студент не имеет отношения к дереву root/child_b
    await db.commit()

    await CourseDependenciesService().add_dependency(
        db, course_id=ids["child_b"], required_course_id=ids["child_a"]
    )

    assert await _cache_state(db, student_id=unrelated, course_id=ids["child_a"]) is None


@pytest.mark.asyncio
async def test_background_tick_backfills_state_for_dependency_added_via_raw_sql(
    db, db_session_factory, dep_graph
):
    """Ключевой тест: фактический путь регрессии tsk-523.

    `course_dependencies` в tsk-523 были записаны ПРЯМЫМ SQL-скриптом
    (`tsk523_apply.py`, протокол `/db-check`), в обход
    `CourseDependenciesService` целиком — синхронный бэкфилл сервиса такую
    запись не увидит вообще. Единственная защита для этого пути — фоновый
    тик `course_dependency_state_cron_service`.
    """
    ids = dep_graph
    student = await _new_student(db, prefix="bg-tick")
    await _enroll(db, student, ids["root"])
    await _complete_task(
        db, user_id=student, task_id=ids["task_a"],
        course_id=ids["child_a"], root_course_id=ids["root"],
    )
    # Прямой SQL — тот же способ, которым фактически была внесена регрессия
    # tsk-523, без единого вызова CourseDependenciesService.
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:c, :r)"
        ),
        {"c": ids["child_b"], "r": ids["child_a"]},
    )
    await db.commit()

    assert await _cache_state(db, student_id=student, course_id=ids["child_a"]) is None, (
        "прямая запись в course_dependencies не должна сама по себе заполнять кеш"
    )

    summary = await course_dependency_state_cron_service.course_dependency_state_cron_tick(
        db_session_factory
    )
    assert summary["locked"] is True
    assert summary["pairs_checked"] >= 1
    assert summary["students_recomputed"] >= 1

    state = await _cache_state(db, student_id=student, course_id=ids["child_a"])
    assert state == "COMPLETED", (
        "фоновый тик не пересчитал student_course_state для зависимости, "
        f"записанной в обход API: state={state!r}"
    )
    assert not await _is_blocked(db, student_id=student, required_course_id=ids["child_a"])
