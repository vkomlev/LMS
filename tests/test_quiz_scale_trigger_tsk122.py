"""
Тесты интерпретации шкал и триггера quiz_scale (tsk-122, ADR-0003, Stage 2).

Покрывают:
- накопление scale_scores по всем квиз-задачам курса у ученика (последний результат
  на задачу, без задвоения повторных попыток);
- интерпретацию argmax (строгий уникальный максимум) и min_score (порог);
- срабатывание триггера quiz_scale в evaluate_rules_for_attempt + идемпотентность
  once_per_student;
- отсутствие срабатывания при ничьей (argmax) и при отсутствии правил (no-op).

Тесты создают данные в dev-БД (Learn.public) и подчищают за собой.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.services import assignment_rules_service as ars

pytestmark = pytest.mark.asyncio


async def _make_student(db) -> int:
    email = f"qscale_{uuid.uuid4().hex[:8]}@example.com"
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, 'tsk122 student') RETURNING id"),
        {"e": email},
    )
    sid = int(r.scalar())
    await db.commit()
    return sid


async def _make_course(db, uid: str | None = None) -> int:
    r = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES (:t, 'auto_check', :uid) RETURNING id"
        ),
        {"t": f"tsk122 {uid or 'quiz'}", "uid": uid},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _make_quiz_task(db, course_id: int) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    tc = (
        '{"type":"SC_Qw","stem":"x","scales":["информатика","python"],'
        '"options":[{"id":"a","text":"a","scores":{"информатика":1}}]}'
    )
    r = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, task_content) "
            "VALUES (:cid, :did, CAST(:tc AS jsonb)) RETURNING id"
        ),
        {"cid": course_id, "did": diff, "tc": tc},
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _make_attempt(db, user_id: int, course_id: int) -> int:
    r = await db.execute(
        text(
            "INSERT INTO attempts (user_id, course_id, finished_at) "
            "VALUES (:u, :c, now()) RETURNING id"
        ),
        {"u": user_id, "c": course_id},
    )
    aid = int(r.scalar())
    await db.commit()
    return aid


async def _write_result(db, *, attempt_id, task_id, user_id, scale_scores: dict) -> None:
    await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, attempt_id, max_score, "
            "is_correct, scale_scores, source_system) "
            "VALUES (0, :u, :t, :a, 1, NULL, CAST(:ss AS jsonb), 'test')"
        ),
        {"u": user_id, "t": task_id, "a": attempt_id, "ss": json.dumps(scale_scores)},
    )
    await db.commit()


async def _make_quiz_rule(db, *, course_id, condition: dict, target_uid: str) -> int:
    code = f"quiz-{uuid.uuid4().hex[:8]}"
    r = await db.execute(
        text(
            "INSERT INTO assignment_rule (code, trigger_event, course_id, condition, target_course_uid) "
            "VALUES (:code, 'quiz_scale', :cid, CAST(:cond AS jsonb), :uid) RETURNING id"
        ),
        {"code": code, "cid": course_id, "cond": json.dumps(condition), "uid": target_uid},
    )
    rid = int(r.scalar())
    await db.commit()
    return rid


async def _cleanup(db, *, course_ids: list[int], student_id: int) -> None:
    for cid in course_ids:
        await db.execute(text("DELETE FROM courses WHERE id = :cid"), {"cid": cid})
    await db.execute(text("DELETE FROM users WHERE id = :sid"), {"sid": student_id})
    await db.commit()


async def test_quiz_scale_argmax_assigns(db):
    """argmax: накоплено {информатика:3, python:1} → правило по информатике назначает курс."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    target_uid = f"wp:vvodnaya-informatika-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    t1 = await _make_quiz_task(db, quiz_course)
    t2 = await _make_quiz_task(db, quiz_course)
    attempt_id = await _make_attempt(db, student_id, quiz_course)
    await _write_result(db, attempt_id=attempt_id, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 2, "python": 0})
    await _write_result(db, attempt_id=attempt_id, task_id=t2, user_id=student_id,
                        scale_scores={"информатика": 1, "python": 1})
    await _make_quiz_rule(db, course_id=quiz_course,
                          condition={"scale": "информатика", "mode": "argmax"},
                          target_uid=target_uid)
    try:
        fired = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired == 1
        enrolled = (
            await db.execute(
                text("SELECT 1 FROM user_courses WHERE user_id = :u AND course_id = :c"),
                {"u": student_id, "c": target_course},
            )
        ).fetchone()
        assert enrolled is not None

        # once_per_student: повтор не назначает второй раз.
        fired2 = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired2 == 0
    finally:
        await _cleanup(db, course_ids=[quiz_course, target_course], student_id=student_id)


async def test_quiz_scale_argmax_tie_no_fire(db):
    """argmax: при ничьей шкал правило не срабатывает (нет уникального победителя)."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    target_uid = f"wp:tie-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    t1 = await _make_quiz_task(db, quiz_course)
    attempt_id = await _make_attempt(db, student_id, quiz_course)
    await _write_result(db, attempt_id=attempt_id, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 2, "python": 2})
    await _make_quiz_rule(db, course_id=quiz_course,
                          condition={"scale": "информатика", "mode": "argmax"},
                          target_uid=target_uid)
    try:
        fired = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired == 0
    finally:
        await _cleanup(db, course_ids=[quiz_course, target_course], student_id=student_id)


async def test_quiz_scale_min_score(db):
    """min_score: правило срабатывает при накопленном балле не ниже порога."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    target_uid = f"wp:threshold-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    t1 = await _make_quiz_task(db, quiz_course)
    attempt_id = await _make_attempt(db, student_id, quiz_course)
    await _write_result(db, attempt_id=attempt_id, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 1, "python": 3})
    await _make_quiz_rule(db, course_id=quiz_course,
                          condition={"scale": "python", "min_score": 3},
                          target_uid=target_uid)
    try:
        fired = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired == 1
    finally:
        await _cleanup(db, course_ids=[quiz_course, target_course], student_id=student_id)


async def test_quiz_scale_min_score_below_threshold_no_fire(db):
    """min_score: ниже порога — не срабатывает."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    target_uid = f"wp:below-{uuid.uuid4().hex[:8]}"
    target_course = await _make_course(db, uid=target_uid)
    t1 = await _make_quiz_task(db, quiz_course)
    attempt_id = await _make_attempt(db, student_id, quiz_course)
    await _write_result(db, attempt_id=attempt_id, task_id=t1, user_id=student_id,
                        scale_scores={"python": 2})
    await _make_quiz_rule(db, course_id=quiz_course,
                          condition={"scale": "python", "min_score": 5},
                          target_uid=target_uid)
    try:
        fired = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired == 0
    finally:
        await _cleanup(db, course_ids=[quiz_course, target_course], student_id=student_id)


async def test_accumulate_last_result_per_task(db):
    """Накопление берёт последний результат на задачу (повтор не задваивает)."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    t1 = await _make_quiz_task(db, quiz_course)
    a1 = await _make_attempt(db, student_id, quiz_course)
    a2 = await _make_attempt(db, student_id, quiz_course)
    # Первый ответ на задачу, затем переответ в новой попытке.
    await _write_result(db, attempt_id=a1, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 2})
    await _write_result(db, attempt_id=a2, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 5})
    try:
        totals = await ars._accumulate_course_scales(
            db, student_id=student_id, course_id=quiz_course
        )
        assert totals == {"информатика": 5}
    finally:
        await _cleanup(db, course_ids=[quiz_course], student_id=student_id)


async def test_quiz_scale_no_rules_noop(db):
    """Без правил quiz_scale движок не делает ничего (no-op)."""
    student_id = await _make_student(db)
    quiz_course = await _make_course(db)
    t1 = await _make_quiz_task(db, quiz_course)
    attempt_id = await _make_attempt(db, student_id, quiz_course)
    await _write_result(db, attempt_id=attempt_id, task_id=t1, user_id=student_id,
                        scale_scores={"информатика": 9})
    try:
        fired = await ars.evaluate_rules_for_attempt(
            db, student_id=student_id, attempt_id=attempt_id
        )
        assert fired == 0
    finally:
        await _cleanup(db, course_ids=[quiz_course], student_id=student_id)
