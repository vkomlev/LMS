"""tsk-663: ручной зачёт не считается работой ученика в статистике.

На проде `task_results` на три четверти состоит из ручных отметок преподавателя
(`source_system = 'manual_teacher'`, tsk-297): у них `is_correct = true` и полный
балл по определению. Пока они попадали в средние, `/task-results/stats/*`
показывали 94.3 % верных вместо 78.6 % и средний балл 1.02 вместо 0.94 — то есть
благополучие, которого нет. Ровно с этой подмены («25 пустых работ, зачтённых на
полный балл») началась tsk-663.

При этом ПРОХОЖДЕНИЕ ручной зачёт даёт настоящее: сумма баллов и
`completion_percentage` считаются по-прежнему по всем источникам, иначе ученик с
перенесённым прогрессом выглядел бы непроходящим при полностью зачтённых курсах
в кабинете.

Проверяем на настоящей БД через сервис — контракт эндпоинтов от него.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.services.task_results_service import TaskResultsService

_TAG = "tsk663"


async def _insert_result(
    db, *, user_id: int, task_id: int, course_id: int, is_correct: bool,
    score: int, source_system: str,
) -> None:
    """Одна строка результата вместе со своей попыткой."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, "
                "  source_system, finished_at) "
                "VALUES (:u, :c, :c, :src, now()) RETURNING id"
            ),
            {"u": user_id, "c": course_id, "src": source_system},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, now(), now(), 0, :src)"
        ),
        {"u": user_id, "t": task_id, "a": attempt_id, "sc": score,
         "ok": is_correct, "src": source_system},
    )


@pytest.fixture
async def graph(db):
    """Ученик, задание и две строки: своя неверная сдача + ручной зачёт."""
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
    task_id = (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "  difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA_COM", "stem": f"{_TAG} условие", "title": ""}),
                "sr": json.dumps({"max_score": 10}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()

    # Сам решал и ошибся.
    await _insert_result(
        db, user_id=user_id, task_id=task_id, course_id=course_id,
        is_correct=False, score=0, source_system="spw_web",
    )
    # Преподаватель зачёл задание вручную — полный балл, ответа нет.
    await _insert_result(
        db, user_id=user_id, task_id=task_id, course_id=course_id,
        is_correct=True, score=10, source_system="manual_teacher",
    )
    await db.commit()
    return {"user_id": user_id, "task_id": task_id, "course_id": course_id}


@pytest.mark.asyncio
async def test_task_stats_ignore_manual_grant(db, graph):
    """Статистика задания: видна только настоящая сдача, и она неверная."""
    stats = await TaskResultsService().get_stats_by_task(db, graph["task_id"])
    assert stats["total_attempts"] == 1, stats
    assert stats["correct_percentage"] == 0.0, stats
    assert stats["average_score"] == 0.0, stats


@pytest.mark.asyncio
async def test_course_stats_ignore_manual_grant(db, graph):
    """То же по курсу: ручной зачёт не поднимает долю верных до 50 %."""
    stats = await TaskResultsService().get_stats_by_course(db, graph["course_id"])
    assert stats["total_attempts"] == 1, stats
    assert stats["correct_percentage"] == 0.0, stats


@pytest.mark.asyncio
async def test_user_stats_keep_manual_grant_in_progress(db, graph):
    """У ученика качество считается по своей сдаче, а пройденное — по обеим строкам."""
    stats = await TaskResultsService().get_stats_by_user(db, graph["user_id"])
    assert stats["total_attempts"] == 1, stats
    assert stats["correct_percentage"] == 0.0, stats
    # Суммы баллов остаются по всем источникам — иначе зачтённое задание
    # выглядело бы непройденным.
    assert stats["total_max_score"] == 20, stats
    assert stats["total_score"] == 10, stats
