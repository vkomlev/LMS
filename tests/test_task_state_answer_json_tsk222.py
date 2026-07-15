"""Integration HTTP-тесты GET /learning/tasks/{id}/state — сохранённый ответ (tsk-222).

Проверяют аддитивные поля last_answer_json / last_is_correct / last_checked_at:
- PASSED: отдаётся ранее отправленный answer_json ученика;
- SA_COM «на проверке» (is_correct=True, checked_at=NULL): answer виден, checked_at=null;
- not_started: все три поля null;
- IDOR: чужой student_id → 403;
- эталон (correct_options/solution) в ответе НЕ появляется.

Паттерн seed'а — из test_me_history_y4.py; дополнительно создаётся attempt,
т.к. compute_task_state требует INNER JOIN attempts (cancelled_at IS NULL).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_user(db):
    email = f"tsk222-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk222", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    access_token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, access_token


async def _pick_active_task(db) -> int:
    row = (
        await db.execute(text("SELECT id FROM tasks WHERE is_active = true LIMIT 1"))
    ).fetchone()
    if row is None:
        pytest.skip("Нет активных задач в БД")
    return int(row[0])


async def _create_attempt(db, *, user_id: int) -> int:
    """Активный (не отменённый) attempt — обязателен для compute_task_state."""
    res = await db.execute(
        text(
            "INSERT INTO attempts (user_id, source_system) "
            "VALUES (:u, 'test') RETURNING id"
        ),
        {"u": user_id},
    )
    aid = res.scalar_one()
    await db.commit()
    return int(aid)


async def _create_task_result(
    db, *, attempt_id: int, user_id: int, task_id: int,
    answer_json: dict, is_correct: bool | None,
    score: int, max_score: int,
) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, attempt_id, submitted_at, count_retry, "
            " received_at, max_score, source_system, is_correct, answer_json, checked_at) "
            "VALUES (:s, :u, :t, :aid, :now, 0, :now, :ms, 'spw', :ic, "
            "        CAST(:answer AS jsonb), :checked) "
            "RETURNING id"
        ),
        {
            "s": score, "u": user_id, "t": task_id, "aid": attempt_id, "now": now,
            "ms": max_score, "ic": is_correct,
            "answer": json.dumps(answer_json),
            "checked": now if is_correct is not None else None,
        },
    )
    rid = res.scalar_one()
    await db.commit()
    return int(rid)


async def _cleanup(db, user_id: int, rids: list[int], attempt_ids: list[int]):
    if rids:
        await db.execute(
            text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids}
        )
    if attempt_ids:
        await db.execute(
            text("DELETE FROM attempts WHERE id = ANY(:a)"), {"a": attempt_ids}
        )
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_state_returns_saved_answer_when_passed(db, client):
    """PASSED-задача отдаёт last_answer_json = отправленный ответ + is_correct/checked_at."""
    uid, token = await _setup_user(db)
    task_id = await _pick_active_task(db)
    answer = {"type": "SA", "response": {"value": "сорок два"}}
    aid = await _create_attempt(db, user_id=uid)
    rids = []
    try:
        rids.append(await _create_task_result(
            db, attempt_id=aid, user_id=uid, task_id=task_id,
            answer_json=answer, is_correct=True, score=10, max_score=10,
        ))
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={uid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "PASSED"
        assert body["last_answer_json"] == answer
        assert body["last_is_correct"] is True
        assert body["last_checked_at"] is not None
        # Эталон в ответ ученика не подмешивается
        assert "correct_options" not in body
        assert "solution_rules" not in body
    finally:
        await _cleanup(db, uid, rids, [aid])


@pytest.mark.asyncio
async def test_state_pending_manual_review_answer_visible(db, client):
    """SA_COM «на проверке»: is_correct=True + checked_at=NULL → ответ виден, checked_at=null."""
    uid, token = await _setup_user(db)
    task_id = await _pick_active_task(db)
    answer = {"type": "SA_COM", "response": {"value": "print(42)", "comment": "готово"}}
    aid = await _create_attempt(db, user_id=uid)
    rids = []
    try:
        # optimistic-PASSED: is_correct=True, score=max_score, но checked_at=NULL
        rids.append(await _create_task_result_pending(
            db, attempt_id=aid, user_id=uid, task_id=task_id,
            answer_json=answer, score=10, max_score=10,
        ))
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={uid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "PASSED"  # optimistic score-ratio 1.0
        assert body["last_answer_json"] == answer
        assert body["last_is_correct"] is True
        assert body["last_checked_at"] is None  # ещё не проверено учителем
    finally:
        await _cleanup(db, uid, rids, [aid])


async def _create_task_result_pending(
    db, *, attempt_id: int, user_id: int, task_id: int,
    answer_json: dict, score: int, max_score: int,
) -> int:
    """task_result с is_correct=True, но checked_at=NULL (optimistic-PASSED на проверке)."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, attempt_id, submitted_at, count_retry, "
            " received_at, max_score, source_system, is_correct, answer_json, checked_at) "
            "VALUES (:s, :u, :t, :aid, :now, 0, :now, :ms, 'spw', true, "
            "        CAST(:answer AS jsonb), NULL) "
            "RETURNING id"
        ),
        {
            "s": score, "u": user_id, "t": task_id, "aid": attempt_id, "now": now,
            "ms": max_score, "answer": json.dumps(answer_json),
        },
    )
    rid = res.scalar_one()
    await db.commit()
    return int(rid)


@pytest.mark.asyncio
async def test_state_not_started_has_null_answer(db, client):
    """Задача без единого task_result → все три поля null, state OPEN."""
    uid, token = await _setup_user(db)
    task_id = await _pick_active_task(db)
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={uid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] in ("OPEN", "IN_PROGRESS")
        assert body["last_answer_json"] is None
        assert body["last_is_correct"] is None
        assert body["last_checked_at"] is None
    finally:
        await _cleanup(db, uid, [], [])


@pytest.mark.asyncio
async def test_state_idor_foreign_student_forbidden(db, client):
    """IDOR: ученик A не может запросить state по student_id ученика B → 403."""
    uid_a, token_a = await _setup_user(db)
    uid_b, _ = await _setup_user(db)
    task_id = await _pick_active_task(db)
    try:
        resp = await client.get(
            f"/api/v1/learning/tasks/{task_id}/state?student_id={uid_b}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 403
    finally:
        await _cleanup(db, uid_a, [], [])
        await _cleanup(db, uid_b, [], [])
