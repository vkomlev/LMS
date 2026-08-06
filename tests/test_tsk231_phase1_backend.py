"""tsk-231 Фаза 1: LMS backend для мини-курсов повторения + блокировки прогресса.

Два самостоятельных набора проверок:

1. Ретроактивное доназначение при добавлении `course_dependencies` к УЖЕ
   идущему курсу (`CourseDependenciesService._enroll_existing_students`).
   tsk-261 закрыл симметричный путь — доназначение зависимостей в момент
   НАЗНАЧЕНИЯ курса ученику. Но методист чаще добавляет зависимость к курсу,
   на котором уже есть ученики — этот путь раньше доназначения не делал:
   `_BLOCKED_COURSES_SQL`/`resolve_next_item` блокируют их немедленно
   (backfill_dependency_state, tsk-541), а required-курса физически нет в
   их `user_courses` — замок без выхода.

2. Обогащение контракта данными о курсе-зависимости (название/uid), которых
   раньше не было — SPW/TG_LMS не могли показать ученику, ЧТО именно нужно
   пройти (только голый числовой ID).

План: docs/specs/2026-08-06-plan-tsk231-mini-kursy-blokirovka.md, Фаза 1.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.course_dependencies_service import CourseDependenciesService
from app.services.learning_engine_service import LearningEngineService
from app.services import me_service


async def _create_student(db, *, prefix: str = "tsk231") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _create_course(db, *, title: str, course_uid: str | None = None) -> int:
    uid = course_uid or f"tsk231-{random.randint(10**8, 10**10)}"
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": uid},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _new_task(db, *, course_id: int, uid: str) -> int:
    """Курс без единого задания/материала тривиально COMPLETED (total_items=0,
    compute_course_state) — для проверки реальной блокировки required-курсу
    нужен хотя бы один непройденный элемент."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    if difficulty_id is None:
        pytest.skip("Нет ни одной difficulty — задание не создать")
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk231"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": uid,
        },
    )
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _add_parent(db, *, course_id: int, parent_course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id) "
            "VALUES (:c, :p) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id},
    )
    await db.commit()


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _enrolled_ids(db, user_id: int) -> set[int]:
    res = await db.execute(
        text("SELECT course_id FROM user_courses WHERE user_id = :u"), {"u": user_id}
    )
    return {int(r[0]) for r in res.fetchall()}


async def _cleanup(db, *, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(
        text("DELETE FROM user_courses WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(
        text("DELETE FROM student_course_state WHERE student_id = ANY(:ids)"),
        {"ids": user_ids},
    )
    await db.execute(
        text("DELETE FROM user_session WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(
        text("DELETE FROM identity_link WHERE user_id = ANY(:ids)"), {"ids": user_ids}
    )
    await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": user_ids})
    if course_ids:
        await db.execute(
            text(
                "DELETE FROM course_dependencies "
                "WHERE course_id = ANY(:ids) OR required_course_id = ANY(:ids)"
            ),
            {"ids": course_ids},
        )
        await db.execute(
            text(
                "DELETE FROM course_parents "
                "WHERE course_id = ANY(:ids) OR parent_course_id = ANY(:ids)"
            ),
            {"ids": course_ids},
        )
        await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": course_ids})
    await db.commit()


# ─────────────────────── 1.1 Ретроактивное доназначение ────────────────────


@pytest.mark.asyncio
async def test_add_dependency_enrolls_already_enrolled_students(db):
    """Ключевой тест фикса: студент зачислен на main ДО добавления зависимости.

    До фикса — main мгновенно блокируется (backfill_dependency_state), а
    mini физически недостижим (его нет в user_courses этого студента).
    """
    main = await _create_course(db, title="tsk231 основной курс")
    mini = await _create_course(db, title="tsk231 мини-курс повторения")
    student = await _create_student(db, prefix="retro")
    await _enroll(db, student, main)
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini
        )
        enrolled = await _enrolled_ids(db, student)
        assert mini in enrolled, (
            "мини-курс обязан быть доназначен уже зачисленному студенту, "
            "иначе блокировка становится замком без выхода"
        )
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_add_dependency_enrollment_is_idempotent(db):
    """Повторное добавление той же зависимости не плодит дублей и не падает."""
    main = await _create_course(db, title="tsk231 idem main")
    mini = await _create_course(db, title="tsk231 idem mini")
    student = await _create_student(db, prefix="idem")
    await _enroll(db, student, main)
    try:
        service = CourseDependenciesService()
        await service.add_dependency(db, course_id=main, required_course_id=mini)
        # Повторный add_dependency (методист кликает ещё раз / ON CONFLICT DO NOTHING в репо).
        await service.add_dependency(db, course_id=main, required_course_id=mini)

        res = await db.execute(
            text("SELECT COUNT(*) FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": student, "c": mini},
        )
        assert int(res.scalar_one()) == 1
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_add_dependency_skips_non_root_required_course(db):
    """required-курс с родителем — не доназначается (триггер БД), но и не роняет вызов.

    Тот же инвариант, что и в tsk-261 (`ensure_dependencies_assigned`),
    теперь и на пути добавления зависимости к идущему курсу.
    """
    main = await _create_course(db, title="tsk231 skip main")
    parent = await _create_course(db, title="tsk231 skip parent")
    mini_child = await _create_course(db, title="tsk231 skip mini (не корень)")
    await _add_parent(db, course_id=mini_child, parent_course_id=parent)
    student = await _create_student(db, prefix="skip")
    await _enroll(db, student, main)
    try:
        # Не должно бросить исключение (INSERT в user_courses уронил бы транзакцию).
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini_child
        )
        enrolled = await _enrolled_ids(db, student)
        assert mini_child not in enrolled, "некорневой курс привязывать нельзя (триггер БД)"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini_child, parent])


@pytest.mark.asyncio
async def test_add_dependency_does_not_enroll_unrelated_students(db):
    """Студент, не зачисленный на main, доназначение не получает."""
    main = await _create_course(db, title="tsk231 unrelated main")
    mini = await _create_course(db, title="tsk231 unrelated mini")
    unrelated = await _create_student(db, prefix="unrelated")
    # НЕ enroll unrelated на main.
    try:
        await CourseDependenciesService().add_dependency(
            db, course_id=main, required_course_id=mini
        )
        enrolled = await _enrolled_ids(db, unrelated)
        assert mini not in enrolled
    finally:
        await _cleanup(db, user_ids=[unrelated], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_bulk_add_dependencies_enrolls_existing_students(db):
    """Тот же фикс для массового эндпоинта (реальный write-путь методиста)."""
    main = await _create_course(db, title="tsk231 bulk main")
    mini_a = await _create_course(db, title="tsk231 bulk mini A")
    mini_b = await _create_course(db, title="tsk231 bulk mini B")
    student = await _create_student(db, prefix="bulk")
    await _enroll(db, student, main)
    try:
        added = await CourseDependenciesService().bulk_add_dependencies(
            db, main, [mini_a, mini_b]
        )
        assert len(added) == 2

        enrolled = await _enrolled_ids(db, student)
        assert {mini_a, mini_b} <= enrolled
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini_a, mini_b])


@pytest.mark.asyncio
async def test_count_affected_students_matches_enrolled(db):
    """Превью-эндпоинт (impact) — число активных студентов дерева main."""
    main = await _create_course(db, title="tsk231 impact main")
    mini = await _create_course(db, title="tsk231 impact mini")
    student_a = await _create_student(db, prefix="impact-a")
    student_b = await _create_student(db, prefix="impact-b")
    unrelated = await _create_student(db, prefix="impact-c")
    await _enroll(db, student_a, main)
    await _enroll(db, student_b, main)
    # unrelated НЕ зачислен на main.
    try:
        count = await CourseDependenciesService().count_affected_students(db, main)
        assert count == 2, f"ожидали 2 (только зачисленные на main), получили {count}"
    finally:
        await _cleanup(
            db, user_ids=[student_a, student_b, unrelated], course_ids=[main, mini]
        )


# ─────────────────────── 1.2/1.3 Обогащение контракта ──────────────────────


@pytest.mark.asyncio
async def test_next_item_blocked_dependency_includes_title_and_uid(db):
    """resolve_next_item отдаёт название+uid курса-зависимости, не только ID."""
    main = await _create_course(db, title="tsk231 next-item main", course_uid="tsk231-next-main")
    mini = await _create_course(
        db, title="Мини-курс: повторение циклов", course_uid="tsk231-next-mini"
    )
    await _new_task(db, course_id=mini, uid=f"tsk231-next-{random.randint(10**8, 10**10)}")
    student = await _create_student(db, prefix="nextitem")
    await _enroll(db, student, main)
    await CourseDependenciesService().add_dependency(db, course_id=main, required_course_id=mini)
    try:
        result = await LearningEngineService().resolve_next_item(
            db, student, root_course_id=main
        )
        assert result.type == "blocked_dependency"
        assert result.dependency_course_id == mini
        assert result.dependency_course_title == "Мини-курс: повторение циклов", (
            "клиент (SPW/TG_LMS) не сможет показать название без этого поля"
        )
        assert result.dependency_course_uid == "tsk231-next-mini"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])


@pytest.mark.asyncio
async def test_syllabus_states_blocked_dependencies_includes_title(db):
    """GET-контракт syllabus-states: blocked_dependencies обогащён, blocked_courses не тронут."""
    main = await _create_course(
        db, title="tsk231 syllabus main", course_uid="tsk231-syl-main"
    )
    mini = await _create_course(
        db, title="Мини-курс: повторение синтаксиса", course_uid="tsk231-syl-mini"
    )
    await _new_task(db, course_id=mini, uid=f"tsk231-syl-{random.randint(10**8, 10**10)}")
    student = await _create_student(db, prefix="syllabus")
    await _enroll(db, student, main)
    await CourseDependenciesService().add_dependency(db, course_id=main, required_course_id=mini)
    try:
        body = await me_service.get_syllabus_states(db, student, main)

        assert main in body["blocked_courses"], body["blocked_courses"]

        dep_rows = [d for d in body["blocked_dependencies"] if d["course_id"] == main]
        assert len(dep_rows) == 1, body["blocked_dependencies"]
        dep = dep_rows[0]
        assert dep["required_course_id"] == mini
        assert dep["required_course_title"] == "Мини-курс: повторение синтаксиса"
        assert dep["required_course_uid"] == "tsk231-syl-mini"
    finally:
        await _cleanup(db, user_ids=[student], course_ids=[main, mini])
