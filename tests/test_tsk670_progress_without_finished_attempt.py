"""tsk-670: прогресс считается по последнему результату, а не по «завершённой попытке».

Попытка в LMS курсового уровня: одна на пару (ученик, курс), копит результаты
многих заданий и закрывается только когда курс пройден до конца. На проде
`finished_at` стоял у 91 попытки из 12 611 (0.7 %), а у ручных зачётов не стоит
никогда — поэтому статистика показывала «прогресс 0 %, пройдено 0» ученику,
решившему 291 задание.

Здесь проверяется именно тот случай, который раньше давал нули: попытка ОТКРЫТА
(`finished_at IS NULL`), а задания по ней уже решены. Правило то же, что в
`learning_engine.compute_task_state` и `me_service`: последний результат по
заданию, попытка не аннулирована.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.services.task_results_service import TaskResultsService

_TAG = "tsk670"


@pytest.fixture
async def graph(db):
    """Ученик, два задания и открытая (незавершённая) попытка с результатами."""
    user_id = (
        await db.execute(
            text(
                "INSERT INTO users (email, password_hash, full_name) "
                "VALUES (:e, NULL, :n) RETURNING id"
            ),
            {"e": f"{_TAG}-{random.randint(10**8, 10**10)}@example.com",
             "n": f"{_TAG}-student"},
        )
    ).scalar()
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level) "
                "VALUES (:t, 'self_guided') RETURNING id"
            ),
            {"t": f"{_TAG} курс"},
        )
    ).scalar()
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()

    async def new_task() -> int:
        return (
            await db.execute(
                text(
                    "INSERT INTO tasks (task_content, solution_rules, course_id, "
                    "  difficulty_id, external_uid, max_score, order_position) "
                    "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                    "RETURNING id"
                ),
                {
                    "tc": json.dumps({"type": "SA", "stem": f"{_TAG} условие", "title": ""}),
                    "sr": json.dumps({"max_score": 10}),
                    "cid": course_id,
                    "did": difficulty_id,
                    "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                },
            )
        ).scalar()

    task_passed = await new_task()
    task_failed = await new_task()

    # Одна ОТКРЫТАЯ курсовая попытка на оба задания — так работает кабинет.
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'spw_web') RETURNING id"
            ),
            {"u": user_id, "c": course_id},
        )
    ).scalar()

    async def result(task_id: int, score: int, is_correct: bool, minutes_ago: int) -> None:
        await db.execute(
            text(
                "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
                "  is_correct, submitted_at, received_at, count_retry, source_system) "
                "VALUES (:u, :t, :a, :sc, 10, :ok, now() - make_interval(mins => :m), "
                "  now() - make_interval(mins => :m), 0, 'spw_web')"
            ),
            {"u": user_id, "t": task_id, "a": attempt_id, "sc": score,
             "ok": is_correct, "m": minutes_ago},
        )

    # Первое задание: сначала ошибся, потом решил — считается последний результат.
    await result(task_passed, 0, False, minutes_ago=30)
    await result(task_passed, 10, True, minutes_ago=10)
    # Второе задание так и осталось неверным.
    await result(task_failed, 0, False, minutes_ago=20)
    await db.commit()
    return {"user_id": user_id, "course_id": course_id,
            "task_passed": task_passed, "task_failed": task_failed}


@pytest.mark.asyncio
async def test_user_progress_counts_open_attempt(db, graph):
    """Попытка не завершена — прогресс всё равно виден: 1 из 2 заданий пройдено."""
    stats = await TaskResultsService().get_stats_by_user(db, graph["user_id"])
    assert stats["passed_tasks_count"] == 1, stats
    assert stats["failed_tasks_count"] == 1, stats
    assert stats["progress_percent"] == 50.0, stats
    # Последний результат первого задания — 10, второго — 0.
    assert stats["current_score"] == 10, stats
    assert stats["last_max_score"] == 20, stats


@pytest.mark.asyncio
async def test_task_progress_counts_last_result(db, graph):
    """По заданию берётся ПОСЛЕДНИЙ результат, а не лучший и не первый."""
    passed = await TaskResultsService().get_stats_by_task(db, graph["task_passed"])
    assert passed["passed_tasks_count"] == 1, passed
    assert passed["progress_percent"] == 100.0, passed

    failed = await TaskResultsService().get_stats_by_task(db, graph["task_failed"])
    assert failed["passed_tasks_count"] == 0, failed
    assert failed["progress_percent"] == 0.0, failed


@pytest.mark.asyncio
async def test_course_progress_counts_open_attempt(db, graph):
    """По курсу — то же правило: два задания с результатом, одно пройдено."""
    stats = await TaskResultsService().get_stats_by_course(db, graph["course_id"])
    assert stats["passed_tasks_count"] == 1, stats
    assert stats["failed_tasks_count"] == 1, stats
    assert stats["progress_percent"] == 50.0, stats


@pytest.mark.asyncio
async def test_cancelled_attempt_still_ignored(db, graph):
    """Аннулированная попытка в прогресс не входит — это правило не менялось."""
    await db.execute(
        text("UPDATE attempts SET cancelled_at = now() WHERE user_id = :u"),
        {"u": graph["user_id"]},
    )
    await db.commit()
    stats = await TaskResultsService().get_stats_by_user(db, graph["user_id"])
    assert stats["passed_tasks_count"] == 0, stats
    assert stats["failed_tasks_count"] == 0, stats
    assert stats["progress_percent"] == 0.0, stats
