# -*- coding: utf-8 -*-
"""tsk-882: теория следующей темы уходит домой, когда текущая закрывается.

Требование оператора 10.09: «теорию желательно изучать дома (материалы и
видео), поэтому если тема подходит к концу — обязательно задавать теорию на
дом». Занятие должно уходить на работу, а не на чтение: человек приходит,
уже зная материал.

Условие «тема подходит к концу» — факт, а не порог в процентах: после выдачи
в теме не остаётся ни одного незавершённого пункта.
"""
from __future__ import annotations

import json
import random

from sqlalchemy import text

from app.models.users import Users
from app.services import homework_service
from app.services.auth import identity_link_service

_TAG = "tsk882"


async def _student(db) -> int:
    email = f"{_TAG}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{_TAG}-ученик", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    await db.commit()
    return user.id


async def _course(db, title: str) -> int:
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, course_uid) "
                    "VALUES (:t, 'self_guided', :u) RETURNING id"
                ),
                {"t": f"{_TAG}-{title}", "u": f"{_TAG}-{random.randint(10**8, 10**10)}"},
            )
        ).scalar_one()
    )
    await db.commit()
    return course_id


async def _link(db, *, parent_id: int, course_id: int, position: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (parent_course_id, course_id, order_number) "
            "VALUES (:p, :c, :o) ON CONFLICT DO NOTHING"
        ),
        {"p": parent_id, "c": course_id, "o": position},
    )
    await db.commit()


async def _material(db, *, course_id: int, title: str, position: int) -> int:
    material_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO materials (course_id, title, type, content, order_position) "
                    "VALUES (:c, :t, 'text', CAST(:body AS jsonb), :o) RETURNING id"
                ),
                {
                    "c": course_id,
                    "t": title,
                    "body": json.dumps({"html": f"<p>{title}</p>"}),
                    "o": position,
                },
            )
        ).scalar_one()
    )
    await db.commit()
    return material_id


async def _task(db, *, course_id: int, position: int) -> int:
    difficulty_id = int(
        (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1")))
        .scalar_one()
    )
    task_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (task_content, solution_rules, course_id, "
                    "  difficulty_id, external_uid, max_score, order_position) "
                    "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                    "        :uid, 10, :pos) RETURNING id"
                ),
                {
                    "tc": json.dumps({"type": "SC", "stem": f"{_TAG} задание {position}"}),
                    "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                    "cid": course_id,
                    "did": difficulty_id,
                    "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                    "pos": position,
                },
            )
        ).scalar_one()
    )
    await db.commit()
    return task_id


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _cleanup(db, *, student_id: int, course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": student_id})
    await db.execute(text("DELETE FROM attempts WHERE user_id = :u"), {"u": student_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": student_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": student_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :u"), {"u": student_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": student_id})
    await db.execute(
        text("DELETE FROM course_parents WHERE course_id = ANY(:i) OR parent_course_id = ANY(:i)"),
        {"i": course_ids},
    )
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


async def _two_topics(db) -> tuple[int, list[int], dict[str, list[int]]]:
    """Корень с двумя темами: у первой одно задание, у второй теория и задание."""
    student_id = await _student(db)
    root_id = await _course(db, "корень")
    first_id = await _course(db, "тема-1")
    second_id = await _course(db, "тема-2")
    await _link(db, parent_id=root_id, course_id=first_id, position=1)
    await _link(db, parent_id=root_id, course_id=second_id, position=2)
    await _enroll(db, student_id=student_id, course_id=root_id)

    ids = {
        "first_tasks": [await _task(db, course_id=first_id, position=i) for i in range(2)],
        "second_materials": [
            await _material(db, course_id=second_id, title=f"теория {i}", position=i)
            for i in range(3)
        ],
        "second_tasks": [await _task(db, course_id=second_id, position=i) for i in range(2)],
    }
    return student_id, [root_id, first_id, second_id], ids


async def test_theory_of_next_topic_goes_home_when_the_topic_closes(db):
    """Выдача добирает последние задания темы — теория следующей едет с ними."""
    student_id, course_ids, ids = await _two_topics(db)
    try:
        # Бюджета хватает ровно на два задания первой темы.
        items = await homework_service._next_items(db, student_id=student_id, limit=2)
        kinds = [(i["kind"], i["item_id"]) for i in items]

        assert ("task", ids["first_tasks"][0]) in kinds
        assert ("task", ids["first_tasks"][1]) in kinds
        for material_id in ids["second_materials"]:
            assert ("material", material_id) in kinds, (
                "тема закрылась, а теория следующей осталась на занятие"
            )
        # Задания следующей темы не берём: домой уходит теория, не работа.
        assert ("task", ids["second_tasks"][0]) not in kinds
    finally:
        await _cleanup(db, student_id=student_id, course_ids=course_ids)


async def test_theory_stays_when_the_topic_is_not_finished(db):
    """Тема не закрывается — вперёд не забегаем, теория остаётся на потом."""
    student_id, course_ids, ids = await _two_topics(db)
    try:
        items = await homework_service._next_items(db, student_id=student_id, limit=1)
        kinds = [(i["kind"], i["item_id"]) for i in items]

        assert kinds == [("task", ids["first_tasks"][0])], (
            "теория следующей темы уехала домой, хотя текущая ещё не пройдена"
        )
    finally:
        await _cleanup(db, student_id=student_id, course_ids=course_ids)


async def test_items_carry_no_service_fields(db):
    """Наружу состав отдаётся прежней формой: kind, item_id, title.

    Тема нужна только внутри набора. Лишний ключ уехал бы в выдачу ДЗ и
    в ответ API, где его никто не ждёт.
    """
    student_id, course_ids, _ = await _two_topics(db)
    try:
        items = await homework_service._next_items(db, student_id=student_id, limit=3)
        assert items, "набор пуст — проверять нечего"
        for item in items:
            assert set(item) == {"kind", "item_id", "title"}, item
    finally:
        await _cleanup(db, student_id=student_id, course_ids=course_ids)
