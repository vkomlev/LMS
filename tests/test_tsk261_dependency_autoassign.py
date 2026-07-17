"""tsk-261 (A2): автоназначение курсов-зависимостей при назначении курса.

Дефект приёмки QA 2026-07-16 — «заблоченный курс так и висит». Курс зависит от
другого (`course_dependencies`); замок снимается только когда required-курс стал
`COMPLETED`. Но если required-курс ученику НЕ назначен — пройти его нельзя, и
замок висит вечно (живой случай: «Python для подростков» требует «Вводный
Python», у QA он не назначен).

Покрывает:
- прямая зависимость доназначается;
- транзитивная цепочка A → B → C;
- цикл A → B → A не вешает обход (БД запрещает только самоссылку);
- зависимость-не-корень пропускается (триггер `trg_check_user_course_no_parents`),
  назначение основного курса при этом не падает;
- идемпотентность: повторное назначение не плодит дублей.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import course_dependencies_enrollment_service as deps_service
from app.services import assignment_rules_service
from app.services.auth import identity_link_service


async def _create_student(db, *, prefix: str = "tsk261a2") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _create_course(db, *, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk261-{random.randint(10**8, 10**10)}"},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _add_dependency(db, *, course_id: int, required_course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:c, :r) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "r": required_course_id},
    )
    await db.commit()


async def _add_parent(db, *, course_id: int, parent_course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id) "
            "VALUES (:c, :p) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id},
    )
    await db.commit()


async def _enrolled_ids(db, user_id: int) -> set[int]:
    res = await db.execute(
        text("SELECT course_id FROM user_courses WHERE user_id = :u"), {"u": user_id}
    )
    return {int(r[0]) for r in res.fetchall()}


async def _cleanup(db, *, user_id: int, course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": user_id})
    await db.execute(
        text("DELETE FROM assignment_event WHERE student_id = :u"), {"u": user_id}
    )
    await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
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


@pytest.mark.asyncio
async def test_direct_dependency_is_autoassigned(db):
    """Назначили курс с зависимостью → зависимость назначена тоже (падал до tsk-261)."""
    user_id = await _create_student(db)
    dep = await _create_course(db, title="tsk261 Вводный")
    main = await _create_course(db, title="tsk261 Основной")
    await _add_dependency(db, course_id=main, required_course_id=dep)
    try:
        await assignment_rules_service.assign_course_to_student(
            db, student_id=user_id, course_id=main, source="manual_teacher"
        )
        enrolled = await _enrolled_ids(db, user_id)
        assert main in enrolled
        assert dep in enrolled, (
            "курс-зависимость обязан быть назначен, иначе замок не снять никогда"
        )
    finally:
        await _cleanup(db, user_id=user_id, course_ids=[main, dep])


@pytest.mark.asyncio
async def test_transitive_chain_is_autoassigned(db):
    """A → B → C: назначаем A, ожидаем B и C."""
    user_id = await _create_student(db)
    c = await _create_course(db, title="tsk261 C")
    b = await _create_course(db, title="tsk261 B")
    a = await _create_course(db, title="tsk261 A")
    await _add_dependency(db, course_id=a, required_course_id=b)
    await _add_dependency(db, course_id=b, required_course_id=c)
    try:
        await assignment_rules_service.assign_course_to_student(
            db, student_id=user_id, course_id=a, source="manual_teacher"
        )
        enrolled = await _enrolled_ids(db, user_id)
        assert {a, b, c} <= enrolled, f"ожидали транзитивную цепочку, получили {enrolled}"
    finally:
        await _cleanup(db, user_id=user_id, course_ids=[a, b, c])


@pytest.mark.asyncio
async def test_dependency_cycle_does_not_hang(db):
    """Цикл A → B → A: обход завершается, оба курса назначены.

    БД запрещает только самоссылку (check_no_self_dependency); взаимный цикл
    возможен, и наивная рекурсия на нём зациклится.
    """
    user_id = await _create_student(db)
    a = await _create_course(db, title="tsk261 cycle A")
    b = await _create_course(db, title="tsk261 cycle B")
    await _add_dependency(db, course_id=a, required_course_id=b)
    await _add_dependency(db, course_id=b, required_course_id=a)
    try:
        required = await deps_service.collect_required_course_ids(db, [a])
        assert b in required
        assert a not in required, "сам курс не должен попадать в свои зависимости"

        await assignment_rules_service.assign_course_to_student(
            db, student_id=user_id, course_id=a, source="manual_teacher"
        )
        enrolled = await _enrolled_ids(db, user_id)
        assert {a, b} <= enrolled
    finally:
        await _cleanup(db, user_id=user_id, course_ids=[a, b])


@pytest.mark.asyncio
async def test_non_root_dependency_skipped_without_breaking_assignment(db):
    """Зависимость с родителем пропускается, основной курс всё равно назначен.

    Триггер trg_check_user_course_no_parents запрещает привязку ученика к
    некорневому курсу — INSERT такой зависимости уронил бы всю транзакцию.
    """
    user_id = await _create_student(db)
    parent = await _create_course(db, title="tsk261 родитель")
    dep_child = await _create_course(db, title="tsk261 зависимость-ребёнок")
    main = await _create_course(db, title="tsk261 основной")
    await _add_parent(db, course_id=dep_child, parent_course_id=parent)
    await _add_dependency(db, course_id=main, required_course_id=dep_child)
    try:
        await assignment_rules_service.assign_course_to_student(
            db, student_id=user_id, course_id=main, source="manual_teacher"
        )
        enrolled = await _enrolled_ids(db, user_id)
        assert main in enrolled, "основной курс должен назначиться несмотря на плохую зависимость"
        assert dep_child not in enrolled, "некорневой курс привязывать нельзя (триггер БД)"
    finally:
        await _cleanup(db, user_id=user_id, course_ids=[main, dep_child, parent])


@pytest.mark.asyncio
async def test_autoassign_is_idempotent(db):
    """Повторное назначение не плодит дублей и не падает."""
    user_id = await _create_student(db)
    dep = await _create_course(db, title="tsk261 idem dep")
    main = await _create_course(db, title="tsk261 idem main")
    await _add_dependency(db, course_id=main, required_course_id=dep)
    try:
        for _ in range(2):
            await assignment_rules_service.assign_course_to_student(
                db, student_id=user_id, course_id=main, source="manual_teacher"
            )
        res = await db.execute(
            text("SELECT COUNT(*) FROM user_courses WHERE user_id = :u"), {"u": user_id}
        )
        assert int(res.scalar_one()) == 2, "ожидали ровно 2 строки: основной + зависимость"
    finally:
        await _cleanup(db, user_id=user_id, course_ids=[main, dep])
