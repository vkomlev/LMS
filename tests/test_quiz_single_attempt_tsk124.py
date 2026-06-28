"""
Тесты правила «одна попытка» для квиз-вопросов SC_Qw/MC_Qw (tsk-124).

Квиз измеряет баллы по шкалам, у него нет «верно/неверно» — повтор бессмыслен
и задвоил бы накопление scale_scores. Поэтому лимит попыток для квиза всегда = 1,
перебивает override/max_attempts, и сервер запрещает повторный ответ.

Покрывают:
- get_effective_attempt_limit: квиз → 1 даже при заданном max_attempts и override;
- обычная задача (SC) лимит не меняет (регрессия);
- compute_task_state: после одного ответа квиз → PASSED (score=max);
- syllabus SQL (me_service): attempts_limit_effective = 1 для квиза, 3 для обычной;
- POST /attempts/{id}/answers: повторный ответ на квиз → 409;
- после блокировки повтора по квизу остаётся ровно один task_result.

Тесты работают с dev-БД (Learn.public) и подчищают за собой.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services.learning_engine_service import DEFAULT_MAX_ATTEMPTS, LearningEngineService
from app.services.me_service import _SYLLABUS_TASKS_SQL

pytestmark = pytest.mark.asyncio

_settings = Settings()
_engine = LearningEngineService()


# ── helpers ─────────────────────────────────────────────────────────────────


async def _make_student(db) -> int:
    email = f"qsingle_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk124 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db) -> int:
    r = await db.execute(
        text(
            "INSERT INTO courses (title, access_level) "
            "VALUES (:t, 'auto_check') RETURNING id"
        ),
        {"t": f"tsk124 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_quiz_task(db, course_id: int, *, max_attempts: int | None = None) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = (
        '{"type":"SC_Qw","stem":"Что ближе?","scales":["информатика","python"],'
        '"options":[{"id":"a","text":"разгадывать","scores":{"информатика":2}},'
        '{"id":"b","text":"игры","scores":{"python":2}}]}'
    )
    sr = '{"max_score":2,"quiz":{"scales":["информатика","python"],"mode":"single"}}'
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules, max_attempts) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb), :ma) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc, "sr": sr, "ma": max_attempts},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _make_sc_task(db, course_id: int, *, max_attempts: int) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = (
        '{"type":"SC","stem":"2+2?","options":['
        '{"id":"a","text":"3"},{"id":"b","text":"4"}]}'
    )
    sr = '{"max_score":1,"correct_options":["b"]}'
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content, solution_rules, max_attempts) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb), :ma) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc, "sr": sr, "ma": max_attempts},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(
        text("DELETE FROM student_task_limit_override WHERE student_id = :sid"),
        {"sid": student_id},
    )
    await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


# ── get_effective_attempt_limit ──────────────────────────────────────────────


async def test_effective_limit_quiz_is_one_over_max_attempts(db):
    """Квиз с заданным max_attempts=3 всё равно даёт лимит = 1."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id, max_attempts=3)
    try:
        limit = await _engine.get_effective_attempt_limit(db, student_id, task_id)
        assert limit == 1
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_effective_limit_quiz_is_one_over_override(db):
    """Квиз перебивает даже персональный override (он бы дал 5, а нужно 1)."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id)
    await db.execute(
        text(
            "INSERT INTO student_task_limit_override (student_id, task_id, max_attempts_override) "
            "VALUES (:sid, :tid, 5)"
        ),
        {"sid": student_id, "tid": task_id},
    )
    await db.commit()
    try:
        limit = await _engine.get_effective_attempt_limit(db, student_id, task_id)
        assert limit == 1
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_effective_limit_regular_task_unchanged(db):
    """Регрессия: обычная SC-задача уважает max_attempts (не затронута)."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_sc_task(db, course_id, max_attempts=2)
    try:
        limit = await _engine.get_effective_attempt_limit(db, student_id, task_id)
        assert limit == 2
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── syllabus SQL (me_service) ────────────────────────────────────────────────


async def test_syllabus_limit_quiz_is_one(db):
    """me_service syllabus-SQL: квиз → attempts_limit_effective=1, SC → default=3."""
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    quiz_id = await _make_quiz_task(db, course_id, max_attempts=3)
    sc_id = await _make_sc_task(db, course_id, max_attempts=3)
    try:
        rows = (
            await db.execute(
                text(_SYLLABUS_TASKS_SQL),
                {"user_id": student_id, "tree_ids": [course_id], "default_max": DEFAULT_MAX_ATTEMPTS},
            )
        ).mappings().all()
        by_task = {r["task_id"]: r["attempts_limit_effective"] for r in rows}
        assert by_task[quiz_id] == 1
        assert by_task[sc_id] == DEFAULT_MAX_ATTEMPTS
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── compute_task_state ───────────────────────────────────────────────────────


async def test_quiz_passed_after_single_answer(client, db):
    """Один ответ на квиз (score=max) → PASSED, attempts_limit_effective=1."""
    api_key = next(iter(_settings.valid_api_keys))
    headers = {"X-API-Key": api_key}
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id)
    try:
        resp = await client.post(
            "/api/v1/attempts",
            json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        attempt_id = resp.json()["id"]

        ans = await client.post(
            f"/api/v1/attempts/{attempt_id}/answers",
            json={"items": [{"task_id": task_id, "answer": {
                "type": "SC_Qw", "response": {"selected_option_ids": ["a"]}}}]},
            headers=headers,
        )
        assert ans.status_code == 200, ans.text

        state = await _engine.compute_task_state(db, student_id, task_id)
        assert state.state == "PASSED"
        assert state.attempts_limit_effective == 1
        assert state.attempts_used == 1
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── серверный запрет повторной попытки (409) ─────────────────────────────────


async def test_second_quiz_answer_rejected_409(client, db):
    """Повторный ответ на квиз → 409, остаётся ровно один task_result."""
    api_key = next(iter(_settings.valid_api_keys))
    headers = {"X-API-Key": api_key}
    student_id = await _make_student(db)
    course_id = await _make_course(db)
    task_id = await _make_quiz_task(db, course_id)
    try:
        resp = await client.post(
            "/api/v1/attempts",
            json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        attempt_id = resp.json()["id"]

        body = {"items": [{"task_id": task_id, "answer": {
            "type": "SC_Qw", "response": {"selected_option_ids": ["a"]}}}]}

        first = await client.post(f"/api/v1/attempts/{attempt_id}/answers", json=body, headers=headers)
        assert first.status_code == 200, first.text

        # Повтор в той же попытке — запрещён.
        second = await client.post(f"/api/v1/attempts/{attempt_id}/answers", json=body, headers=headers)
        assert second.status_code == 409, second.text

        # И даже в новой попытке по тому же курсу — тоже запрещён.
        resp3 = await client.post(
            "/api/v1/attempts",
            json={"user_id": student_id, "course_id": course_id, "source_system": "test"},
            headers=headers,
        )
        attempt2 = resp3.json()["id"]
        third = await client.post(f"/api/v1/attempts/{attempt2}/answers", json=body, headers=headers)
        assert third.status_code == 409, third.text

        cnt = (
            await db.execute(
                text("SELECT COUNT(*) FROM task_results WHERE task_id = :tid AND user_id = :uid"),
                {"tid": task_id, "uid": student_id},
            )
        ).scalar()
        assert cnt == 1
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)
