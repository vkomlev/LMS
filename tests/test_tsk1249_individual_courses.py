"""tsk-1249: индивидуальный курс ученику из кабинета методиста.

Главные инварианты:
1. Выдача — одна транзакция: курс, запись, замки появляются вместе или никак.
2. Замок точечный: основной курс закрыт у адресата и открыт у соседа по курсу.
3. Снятие выдачи снимает замок.
"""
from __future__ import annotations

import random

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.models.users import Users
from app.services import individual_courses_service as svc
from app.services import me_service
from app.services.auth import identity_link_service
from app.services.learning_engine_service import LearningEngineService


async def _student(db) -> int:
    email = f"tsk1249-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk1249-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    cid = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid) "
                "VALUES (:t, 'self_guided', :u) RETURNING id"
            ),
            {"t": title, "u": f"tsk1249-{random.randint(10**8, 10**10)}"},
        )
    ).scalar_one()
    await db.commit()
    return int(cid)


async def _task(db, course_id: int) -> None:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    if did is None:
        pytest.skip("Нет difficulties")
    await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :c, :d, :u)"
        ),
        {"tc": '{"type": "SA", "question": "tsk1249"}', "c": course_id, "d": did,
         "u": f"tsk1249-{random.randint(10**8, 10**10)}"},
    )
    await db.commit()


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, true)"),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _blocked(db, user_id: int, root_id: int) -> bool:
    """Оба пути к ответу «закрыт ли курс»: движок и оглавление."""
    engine = LearningEngineService()
    nxt = await engine.resolve_next_item(db, user_id, root_course_id=root_id)
    rows = (
        await db.execute(
            text(me_service._BLOCKED_COURSES_SQL),
            {"user_id": user_id, "tree_ids": [root_id]},
        )
    ).all()
    assert (nxt.type == "blocked_dependency") == bool(rows), "движок и оглавление разошлись"
    return bool(rows)


async def _cleanup(db, users: list[int], courses: list[int]) -> None:
    await db.rollback()
    for sql in (
        "DELETE FROM user_courses WHERE user_id = ANY(:u)",
        "DELETE FROM student_course_state WHERE student_id = ANY(:u)",
        "DELETE FROM attempts WHERE user_id = ANY(:u)",
        "DELETE FROM course_dependencies WHERE course_id = ANY(:c) OR required_course_id = ANY(:c)",
        "DELETE FROM course_parents WHERE course_id = ANY(:c) OR parent_course_id = ANY(:c)",
        "DELETE FROM tasks WHERE course_id = ANY(:c)",
        "DELETE FROM courses WHERE id = ANY(:c)",
        "DELETE FROM user_session WHERE user_id = ANY(:u)",
        "DELETE FROM identity_link WHERE user_id = ANY(:u)",
        "DELETE FROM users WHERE id = ANY(:u)",
    ):
        await db.execute(text(sql), {"u": users, "c": courses})
    await db.commit()


@pytest.mark.asyncio
async def test_build_from_topics_locks_only_addressee(db):
    """Собранный из тем курс закрывает основной курс адресату, соседу — нет."""
    main = await _course(db, "tsk1249 основной")
    topic_a = await _course(db, "tsk1249 тема A")
    topic_b = await _course(db, "tsk1249 тема B")
    for c in (main, topic_a, topic_b):
        await _task(db, c)
    kirill, neighbour = await _student(db), await _student(db)
    await _enroll(db, kirill, main)
    await _enroll(db, neighbour, main)
    created: list[int] = []
    try:
        cid = await svc.issue(
            db, kirill, course_id=None, title="Повторение", topic_ids=[topic_b, topic_a],
            lock_root_ids=None, allow_foreign_topics=True,
        )
        created.append(cid)
        order = (
            await db.execute(
                text("SELECT course_id FROM course_parents WHERE parent_course_id=:p ORDER BY order_number"),
                {"p": cid},
            )
        ).scalars().all()
        assert order == [topic_b, topic_a]
        assert await _blocked(db, kirill, main)
        assert not await _blocked(db, neighbour, main)

        items = await svc.list_for_student(db, kirill)
        assert [(i.course_id, i.locked_root_ids, i.state) for i in items] == [
            (cid, [main], "NOT_STARTED")
        ]
        assert await svc.list_for_student(db, neighbour) == []

        await svc.withdraw(db, kirill, cid)
        assert not await _blocked(db, kirill, main)
        assert [i.is_active for i in await svc.list_for_student(db, kirill)] == [False]
    finally:
        await _cleanup(db, [kirill, neighbour], [main, topic_a, topic_b, *created])


@pytest.mark.asyncio
async def test_issue_ready_course_and_reissue_after_withdraw(db):
    """Готовый курс выдаётся с замком; снятый выдаётся снова без дублей."""
    main = await _course(db, "tsk1249 основной")
    ready = await _course(db, "tsk1249 готовый мини-курс")
    await _task(db, main)
    await _task(db, ready)
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    try:
        await svc.issue(db, kirill, course_id=ready, title=None, topic_ids=None, lock_root_ids=[main])
        assert await _blocked(db, kirill, main)
        await svc.withdraw(db, kirill, ready)
        await svc.issue(db, kirill, course_id=ready, title=None, topic_ids=None, lock_root_ids=None)
        assert await _blocked(db, kirill, main)
        deps = (
            await db.execute(
                text("SELECT count(*) FROM course_dependencies WHERE required_course_id=:r"), {"r": ready}
            )
        ).scalar()
        assert deps == 1
    finally:
        await _cleanup(db, [kirill], [main, ready])


@pytest.mark.asyncio
async def test_foreign_topics_need_confirmation(db):
    """tsk-1330: тема не из курсов ученика — 409 foreign_topics; своя тема и подтверждённая чужая проходят."""
    main = await _course(db, "tsk1330 основной")
    own_topic = await _course(db, "tsk1330 своя тема")
    other_root = await _course(db, "tsk1330 мехатроника")
    foreign_topic = await _course(db, "tsk1330 чужая тема")
    for child, parent in ((own_topic, main), (foreign_topic, other_root)):
        await db.execute(
            text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
            {"c": child, "p": parent},
        )
    await db.commit()
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    created: list[int] = []
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.issue(
                db, kirill, course_id=None, title="Повторение",
                topic_ids=[own_topic, foreign_topic], lock_root_ids=None,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "foreign_topics"
        assert [t["id"] for t in exc.value.detail["topics"]] == [foreign_topic]
        assert exc.value.detail["topics"][0]["source_titles"] == ["tsk1330 мехатроника"]
        assert await svc.list_for_student(db, kirill) == []

        created.append(await svc.issue(
            db, kirill, course_id=None, title="Своё", topic_ids=[own_topic], lock_root_ids=None,
        ))
        await svc.withdraw(db, kirill, created[-1])
        # Чужая тема, выданная раньше с подтверждением, «своей» не становится.
        created.append(await svc.issue(
            db, kirill, course_id=None, title="Подтверждено", topic_ids=[foreign_topic],
            lock_root_ids=[main], allow_foreign_topics=True,
        ))
        await db.execute(
            text("UPDATE courses SET course_uid=:u WHERE id=:c"),
            {"u": f"ind:tsk1330-{random.randint(10**8, 10**10)}", "c": created[-1]},
        )
        await db.commit()
        with pytest.raises(HTTPException) as again:
            await svc.issue(
                db, kirill, course_id=None, title="Ещё раз", topic_ids=[foreign_topic],
                lock_root_ids=[main],
            )
        assert again.value.detail["code"] == "foreign_topics"
        await svc.withdraw(db, kirill, created[-1])
        created.append(await svc.issue(
            db, kirill, course_id=None, title="Подтверждено", topic_ids=[foreign_topic],
            lock_root_ids=[main], allow_foreign_topics=True,
        ))
    finally:
        await _cleanup(db, [kirill], [main, own_topic, other_root, foreign_topic, *created])


@pytest.mark.asyncio
async def test_failure_leaves_nothing_behind(db):
    """Закрыть чужой курс нельзя — и новый курс не должен остаться в базе."""
    main = await _course(db, "tsk1249 основной")
    foreign = await _course(db, "tsk1249 чужой")
    topic = await _course(db, "tsk1249 тема")
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.issue(
                db, kirill, course_id=None, title="Повторение", topic_ids=[topic],
                lock_root_ids=[foreign], allow_foreign_topics=True,
            )
        assert exc.value.status_code == 409
        left = (
            await db.execute(
                text("SELECT count(*) FROM course_parents WHERE course_id=:t"), {"t": topic}
            )
        ).scalar()
        assert left == 0
        assert await svc.list_for_student(db, kirill) == []
    finally:
        await _cleanup(db, [kirill], [main, foreign, topic])


@pytest.mark.asyncio
async def test_rejects_both_or_neither_and_nested_course(db):
    """Курс и темы взаимоисключающие; вложенный курс выдать нельзя."""
    main = await _course(db, "tsk1249 основной")
    child = await _course(db, "tsk1249 вложенный")
    await db.execute(
        text("INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"),
        {"c": child, "p": main},
    )
    await db.commit()
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    try:
        for kwargs in (
            dict(course_id=None, title="x", topic_ids=None),
            dict(course_id=child, title="x", topic_ids=[main]),
        ):
            with pytest.raises(HTTPException) as exc:
                await svc.issue(db, kirill, lock_root_ids=None, **kwargs)
            assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await svc.issue(db, kirill, course_id=child, title=None, topic_ids=None, lock_root_ids=None)
        assert exc.value.status_code == 409
    finally:
        await _cleanup(db, [kirill], [main, child])


@pytest.mark.asyncio
async def test_main_course_cannot_be_a_topic(db):
    """Ревью №1: основной курс (на него записаны) темой стать не может — иначе тупик."""
    main = await _course(db, "tsk1249 основной")
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.issue(
                db, kirill, course_id=None, title="Повторение", topic_ids=[main], lock_root_ids=None,
            )
        assert exc.value.status_code == 409
        parents = (
            await db.execute(text("SELECT count(*) FROM course_parents WHERE course_id=:c"), {"c": main})
        ).scalar()
        assert parents == 0
    finally:
        await _cleanup(db, [kirill], [main])


@pytest.mark.asyncio
async def test_shared_ready_course_does_not_lock_other_holders(db):
    """Ревью №2: новый замок на общий готовый курс задел бы других держателей — отказ."""
    main = await _course(db, "tsk1249 основной")
    shared = await _course(db, "tsk1249 общий мини-курс")
    await _task(db, main)
    await _task(db, shared)
    kirill, other = await _student(db), await _student(db)
    for u in (kirill, other):
        await _enroll(db, u, main)
    await _enroll(db, other, shared)
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.issue(db, kirill, course_id=shared, title=None, topic_ids=None, lock_root_ids=None)
        assert exc.value.status_code == 409
        assert not await _blocked(db, other, main)
        assert await svc.list_for_student(db, kirill) == []

        # Замок, поставленный намеренно раньше, новой выдаче не мешает.
        await db.execute(
            text("INSERT INTO course_dependencies (course_id, required_course_id, auto_assign) VALUES (:m, :s, false)"),
            {"m": main, "s": shared},
        )
        await db.commit()
        await svc.issue(db, kirill, course_id=shared, title=None, topic_ids=None, lock_root_ids=None)
        assert await _blocked(db, kirill, main)
    finally:
        await _cleanup(db, [kirill, other], [main, shared])


@pytest.mark.asyncio
async def test_reissue_after_withdraw_checks_alumni(db, monkeypatch):
    """Ревью №3: возврат снятой выдачи проходит те же проверки, что и новая."""
    main = await _course(db, "tsk1249 основной")
    ready = await _course(db, "tsk1249 готовый")
    await _task(db, ready)
    kirill = await _student(db)
    await _enroll(db, kirill, main)
    try:
        await svc.issue(db, kirill, course_id=ready, title=None, topic_ids=None, lock_root_ids=None)
        await svc.withdraw(db, kirill, ready)

        async def _alumni(*_a, **_k):
            raise HTTPException(409, "выпускник")

        monkeypatch.setattr(svc.alumni_enrollment_guard, "assert_not_alumni", _alumni)
        with pytest.raises(HTTPException) as exc:
            await svc.issue(db, kirill, course_id=ready, title=None, topic_ids=None, lock_root_ids=None)
        assert exc.value.status_code == 409
        assert [i.is_active for i in await svc.list_for_student(db, kirill)] == [False]
    finally:
        await _cleanup(db, [kirill], [main, ready])
