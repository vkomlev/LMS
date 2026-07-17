"""tsk-261 (A1): /me/courses считает прогресс по ОТКРЫТЫМ попыткам.

Регресс, который ловит дефект приёмки QA 2026-07-16:
«Задачи все пройдены — но стоит по ним ноль. Прогресс по курсу всё равно 37%».

Первопричина: `_COURSES_PROGRESS_SQL` фильтровал `attempts.finished_at IS NOT NULL`.
Но `attempts` в LMS — КУРСОВОГО уровня: одна попытка на пару (ученик, курс) копит
результаты многих задач и остаётся открытой, пока ученик проходит курс —
`finished_at` ей никто не проставляет (на проде на момент правки: завершено
78 попыток из 352). Из-за фильтра всё, что ученик решает прямо сейчас, в процент
не попадало: дерево курса показывало задачи пройденными (там своя реализация
«пройдено», без фильтра), а счётчик рядом давал ноль.

До правки первый тест падает: tasks_done == 0 и percent считается по материалам.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _create_student(db, *, prefix: str = "tsk261") -> tuple[int, str]:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-stud", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _pick_course_with_tasks(db) -> tuple[int, list[int]]:
    """КОРНЕВОЙ курс со своими задачами и без подкурсов.

    Корневой (нет строки в `course_parents` как child) — иначе триггер БД не даёт
    записать ученика: «Teachers and students can only be linked to courses without
    parents». Без подкурсов — чтобы дерево курса состояло только из его задач и
    прогресс был предсказуем.
    """
    res = await db.execute(
        text(
            "SELECT t.course_id, COUNT(*) AS n "
            "FROM tasks t "
            "WHERE t.is_active = true "
            "  AND t.requirement_level IN ('required','skippable') "
            "  AND NOT EXISTS (SELECT 1 FROM course_parents cp "
            "                  WHERE cp.course_id = t.course_id) "
            "  AND NOT EXISTS (SELECT 1 FROM course_parents cp "
            "                  WHERE cp.parent_course_id = t.course_id) "
            "  AND NOT EXISTS (SELECT 1 FROM course_dependencies cd "
            "                  WHERE cd.course_id = t.course_id) "
            "GROUP BY t.course_id HAVING COUNT(*) >= 2 "
            "ORDER BY t.course_id LIMIT 1"
        )
    )
    row = res.fetchone()
    if row is None:
        pytest.skip("В dev-БД нет корневого курса без подкурсов с ≥2 активными задачами")
    course_id = int(row[0])
    res2 = await db.execute(
        text(
            "SELECT id FROM tasks WHERE course_id = :c AND is_active = true "
            "  AND requirement_level IN ('required','skippable') "
            "ORDER BY id LIMIT 2"
        ),
        {"c": course_id},
    )
    return course_id, [int(r[0]) for r in res2.fetchall()]


async def _open_attempt(db, *, user_id: int, course_id: int) -> int:
    """Открытая (finished_at IS NULL) course-level попытка — как на проде."""
    res = await db.execute(
        text(
            "INSERT INTO attempts (user_id, course_id, source_system, finished_at, cancelled_at) "
            "VALUES (:u, :c, 'spw', NULL, NULL) RETURNING id"
        ),
        {"u": user_id, "c": course_id},
    )
    aid = int(res.scalar_one())
    await db.commit()
    return aid


async def _result(db, *, user_id: int, task_id: int, attempt_id: int, score: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, attempt_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct, checked_at) "
            "VALUES (:s, :u, :t, :a, NOW(), 0, NOW(), 10, 'spw', :ic, NULL) RETURNING id"
        ),
        {"s": score, "u": user_id, "t": task_id, "a": attempt_id, "ic": score >= 5},
    )
    rid = int(res.scalar_one())
    await db.commit()
    return rid


async def _cleanup(db, *, user_id: int, attempt_ids: list[int], result_ids: list[int]) -> None:
    if result_ids:
        await db.execute(
            text("DELETE FROM task_results WHERE id = ANY(:ids)"), {"ids": result_ids}
        )
    if attempt_ids:
        await db.execute(
            text("DELETE FROM attempts WHERE id = ANY(:ids)"), {"ids": attempt_ids}
        )
    for tbl in ("user_courses", "user_session", "identity_link"):
        await db.execute(
            text(f"DELETE FROM {tbl} WHERE user_id = :u"), {"u": user_id}
        )
    await db.commit()


async def _get_course(client, token: str, course_id: int) -> dict | None:
    resp = await client.get(
        "/api/v1/me/courses", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["course_id"] == course_id:
            return c
    return None


@pytest.mark.asyncio
async def test_open_attempt_result_counts_in_progress(db, client):
    """Верный ответ в ОТКРЫТОЙ попытке даёт tasks_done ≥ 1 (падал до tsk-261)."""
    user_id, token = await _create_student(db)
    course_id, task_ids = await _pick_course_with_tasks(db)
    await _enroll(db, user_id, course_id)
    aid = await _open_attempt(db, user_id=user_id, course_id=course_id)
    rid = await _result(db, user_id=user_id, task_id=task_ids[0], attempt_id=aid, score=10)
    try:
        course = await _get_course(client, token, course_id)
        assert course is not None, "курс должен быть в /me/courses после enroll"
        progress = course["progress"]
        assert progress["tasks_done"] >= 1, (
            "результат из открытой course-level попытки обязан считаться пройденным; "
            f"получено {progress}"
        )
        assert progress["percent"] > 0, progress
    finally:
        await _cleanup(db, user_id=user_id, attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_open_attempt_failed_result_not_counted(db, client):
    """Неверный ответ не должен считаться пройденным (порог 0.5)."""
    user_id, token = await _create_student(db)
    course_id, task_ids = await _pick_course_with_tasks(db)
    await _enroll(db, user_id, course_id)
    aid = await _open_attempt(db, user_id=user_id, course_id=course_id)
    rid = await _result(db, user_id=user_id, task_id=task_ids[0], attempt_id=aid, score=1)
    try:
        course = await _get_course(client, token, course_id)
        assert course is not None
        assert course["progress"]["tasks_done"] == 0, course["progress"]
    finally:
        await _cleanup(db, user_id=user_id, attempt_ids=[aid], result_ids=[rid])


@pytest.mark.asyncio
async def test_last_result_wins_over_earlier(db, client):
    """Считается ПОСЛЕДНИЙ результат по задаче: провал после успеха → не пройдено."""
    user_id, token = await _create_student(db)
    course_id, task_ids = await _pick_course_with_tasks(db)
    await _enroll(db, user_id, course_id)
    aid = await _open_attempt(db, user_id=user_id, course_id=course_id)
    rid_ok = await _result(db, user_id=user_id, task_id=task_ids[0], attempt_id=aid, score=10)
    rid_bad = await _result(db, user_id=user_id, task_id=task_ids[0], attempt_id=aid, score=0)
    try:
        course = await _get_course(client, token, course_id)
        assert course is not None
        assert course["progress"]["tasks_done"] == 0, (
            "последний результат — провал, задача не пройдена; "
            f"получено {course['progress']}"
        )
    finally:
        await _cleanup(db, user_id=user_id, attempt_ids=[aid], result_ids=[rid_ok, rid_bad])


@pytest.mark.asyncio
async def test_cancelled_attempt_result_ignored(db, client):
    """Результат из аннулированной попытки не считается (единственный фильтр)."""
    user_id, token = await _create_student(db)
    course_id, task_ids = await _pick_course_with_tasks(db)
    await _enroll(db, user_id, course_id)
    aid = await _open_attempt(db, user_id=user_id, course_id=course_id)
    rid = await _result(db, user_id=user_id, task_id=task_ids[0], attempt_id=aid, score=10)
    await db.execute(
        text("UPDATE attempts SET cancelled_at = NOW() WHERE id = :a"), {"a": aid}
    )
    await db.commit()
    try:
        course = await _get_course(client, token, course_id)
        assert course is not None
        assert course["progress"]["tasks_done"] == 0, course["progress"]
    finally:
        await _cleanup(db, user_id=user_id, attempt_ids=[aid], result_ids=[rid])
