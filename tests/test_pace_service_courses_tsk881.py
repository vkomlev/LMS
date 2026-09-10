# -*- coding: utf-8 -*-
"""tsk-881: служебные курсы не считаются учебной работой при расчёте нормы.

Служебный курс (`courses.is_service`, tsk-877) объясняет устройство сервиса и
экзамена либо меряет вход. Проходят его один раз, обычно в первую неделю, и
его два-три десятка пунктов давали всплеск недельного темпа — а от темпа
поднимается и потолок нормы, и норматив. Человеку прибавляли работы за то,
что он прочитал правила школы.

Замер на боевых 10.09, недели фактического темпа:

    Редько   [85, 61] -> [10, 61]   темп 73  -> 35.5
    Мачула   [152]    -> [77]       темп 152 -> 77

Второй тест — про начало обучения. Правка темпа без него выходит боком:
человек в первую неделю проходит вводный курс, во вторую берётся за предмет,
и окно, отсчитанное от вводного, включает неделю без учебной работы. Медиана
[0, N] даёт половину настоящего темпа, [0, 0, N] — ноль.
"""
from __future__ import annotations

import json
import random

from sqlalchemy import text

from app.models.users import Users
from app.services import homework_volume_service
from app.services.auth import identity_link_service

_TAG = "tsk881"


async def _student(db, name: str) -> int:
    email = f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=f"{_TAG}-{name}", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    await db.commit()
    return user.id


async def _course(db, *, is_service: bool) -> int:
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, course_uid, is_service) "
                    "VALUES (:t, 'self_guided', :u, :s) RETURNING id"
                ),
                {
                    "t": f"{_TAG}-{'служебный' if is_service else 'учебный'}",
                    "u": f"{_TAG}-{random.randint(10**8, 10**10)}",
                    "s": is_service,
                },
            )
        ).scalar_one()
    )
    await db.commit()
    return course_id


async def _tasks(db, *, course_id: int, count: int) -> list[int]:
    difficulty_id = int(
        (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1")))
        .scalar_one()
    )
    ids: list[int] = []
    for position in range(count):
        ids.append(
            int(
                (
                    await db.execute(
                        text(
                            "INSERT INTO tasks (task_content, solution_rules, course_id, "
                            "  difficulty_id, external_uid, max_score, order_position) "
                            "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, "
                            "        :uid, 10, :pos) RETURNING id"
                        ),
                        {
                            "tc": json.dumps({"type": "SC", "stem": f"{_TAG} {position}"}),
                            "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                            "cid": course_id,
                            "did": difficulty_id,
                            "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                            "pos": position,
                        },
                    )
                ).scalar_one()
            )
        )
    await db.commit()
    return ids


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _solve(db, *, student_id: int, course_id: int, task_ids: list[int], days_ago: int) -> None:
    """Сдачи ученика на заданиях курса, все верные, `days_ago` дней назад."""
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id) VALUES (:u, :c) RETURNING id"
                ),
                {"u": student_id, "c": course_id},
            )
        ).scalar_one()
    )
    for task_id in task_ids:
        await db.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                "  is_correct, submitted_at, received_at, source_system) "
                "VALUES (:u, :t, :a, 10, 10, true, now() - make_interval(days => :d), "
                "        now() - make_interval(days => :d), 'spw_web')"
            ),
            {"u": student_id, "t": task_id, "a": attempt_id, "d": days_ago},
        )
    await db.commit()


async def _cleanup(db, *, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(
        text(
            "DELETE FROM homework_item WHERE homework_id IN "
            "  (SELECT id FROM homework_assignment WHERE student_id = ANY(:i))"
        ),
        {"i": user_ids},
    )
    await db.execute(
        text("DELETE FROM homework_assignment WHERE student_id = ANY(:i)"), {"i": user_ids}
    )
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_courses WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


async def test_service_course_work_does_not_inflate_the_pace(db):
    """Двое одинаковых учеников; одному добавлен пройденный служебный курс.

    Учебная работа у обоих одна и та же — значит и темп обязан совпасть.
    """
    plain_id = await _student(db, "plain")
    with_service_id = await _student(db, "service")
    study_id = await _course(db, is_service=False)
    service_id = await _course(db, is_service=True)
    try:
        study_tasks = await _tasks(db, course_id=study_id, count=40)
        service_tasks = await _tasks(db, course_id=service_id, count=30)

        for student_id in (plain_id, with_service_id):
            await _enroll(db, student_id=student_id, course_id=study_id)
            await _solve(
                db, student_id=student_id, course_id=study_id,
                task_ids=study_tasks[:10], days_ago=3,
            )

        # Второй ещё и прошёл служебный курс — целиком, как это и бывает.
        await _enroll(db, student_id=with_service_id, course_id=service_id)
        await _solve(
            db, student_id=with_service_id, course_id=service_id,
            task_ids=service_tasks, days_ago=3,
        )

        plain = await homework_volume_service.compute(db, student_id=plain_id)
        with_service = await homework_volume_service.compute(db, student_id=with_service_id)

        assert with_service.fact_per_week == plain.fact_per_week, (
            "служебный курс поднял темп: "
            f"{with_service.fact_per_week} против {plain.fact_per_week}"
        )
        assert with_service.target_per_week == plain.target_per_week, (
            "служебный курс поднял норму домашней работы"
        )
    finally:
        await _cleanup(
            db, user_ids=[plain_id, with_service_id],
            course_ids=[study_id, service_id],
        )


async def test_start_of_learning_ignores_the_service_course(db):
    """Окно темпа отсчитывается от первой УЧЕБНОЙ работы, а не от вводной.

    Ученик три недели назад прошёл служебный курс, потом была пауза, а вчера
    он взялся за предмет. Если начало считать по служебной сдаче, окно выйдет
    в три недели — [0, 0, N] — и медиана обнулит настоящий темп.
    """
    student_id = await _student(db, "starter")
    study_id = await _course(db, is_service=False)
    service_id = await _course(db, is_service=True)
    try:
        study_tasks = await _tasks(db, course_id=study_id, count=40)
        service_tasks = await _tasks(db, course_id=service_id, count=24)

        await _enroll(db, student_id=student_id, course_id=service_id)
        await _solve(
            db, student_id=student_id, course_id=service_id,
            task_ids=service_tasks, days_ago=20,
        )
        await _enroll(db, student_id=student_id, course_id=study_id)
        await _solve(
            db, student_id=student_id, course_id=study_id,
            task_ids=study_tasks[:20], days_ago=1,
        )

        plan = await homework_volume_service.compute(db, student_id=student_id)

        assert plan.fact_per_week > 0, (
            "темп обнулился: окно отсчитано от служебного курса и захватило "
            "неделю, в которую учебной работы не было"
        )
    finally:
        await _cleanup(
            db, user_ids=[student_id], course_ids=[study_id, service_id],
        )
