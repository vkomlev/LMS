"""Integration тесты GET /api/v1/task-results/by-pending-review.

tsk-247: обязательная очередь (review_kind=mandatory) = TA либо SA_COM с
manual_review_required=true. Автопроверенные MC/SC/SA отфильтрованы. Предикат
общий с claim-next — см. `teacher_queue_service.mandatory_review_sql`.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users


def _api_key_qs() -> str:
    """Legacy `get_db` ожидает api_key в QUERY-параметре `?api_key=…`,
    не в header. Возвращаем готовый qs-фрагмент для конкатенации.
    """
    key = os.environ.get("VALID_API_KEYS", "").split(",")[0].strip()
    if not key:
        pytest.skip("VALID_API_KEYS не задан в .env — пропускаем")
    return f"api_key={key}"


async def _create_task(
    db, *, course_id: int, type_: str, manual: bool | None = None
) -> int:
    """:param manual: solution_rules.manual_review_required (tsk-247)."""
    rules: dict = {"max_score": 10}
    if manual is not None:
        rules["manual_review_required"] = manual
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), :cid, 1) RETURNING id"
        ),
        {
            "ext": f"y42lp-test-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "rules": json.dumps(rules),
            "cid": course_id,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_student(db) -> int:
    u = Users(
        email=f"y42lp-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42lp-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_tr(db, *, user_id: int, task_id: int, is_correct: bool | None) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct) "
            "VALUES (0, :u, :t, :now, 0, :now, 10, 'spw', :ic) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, student_id: int, task_ids: list[int], rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:s"), {"s": student_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:s"), {"s": student_id})
    await db.commit()


@pytest.mark.asyncio
async def test_list_pending_review_excludes_auto_checked_mc(db, client):
    """GET /by-pending-review НЕ возвращает MC с is_correct=False."""
    qs = _api_key_qs()
    student_id = await _create_student(db)
    mc_task = await _create_task(db, course_id=1, type_="MC")
    mc_rid = await _create_tr(db, user_id=student_id, task_id=mc_task, is_correct=False)
    try:
        resp = await client.get(
            f"/api/v1/task-results/by-pending-review?{qs}&user_id={student_id}&limit=100",
        )
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert mc_rid not in ids, (
            f"Auto-checked MC rid={mc_rid} не должен быть в pending review list"
        )
    finally:
        await _cleanup(db, student_id=student_id, task_ids=[mc_task], rids=[mc_rid])


@pytest.mark.asyncio
async def test_list_pending_review_returns_only_sa_com_ta(db, client):
    """Обязательная очередь: SA_COM с manual_review_required=true — да,
    авто-проверенный SA — нет (tsk-247)."""
    qs = _api_key_qs()
    student_id = await _create_student(db)
    sa_com_task = await _create_task(db, course_id=1, type_="SA_COM", manual=True)
    sa_com_rid = await _create_tr(
        db, user_id=student_id, task_id=sa_com_task, is_correct=None
    )
    sa_task = await _create_task(db, course_id=1, type_="SA")
    sa_rid = await _create_tr(
        db, user_id=student_id, task_id=sa_task, is_correct=True
    )
    try:
        resp = await client.get(
            f"/api/v1/task-results/by-pending-review?{qs}&user_id={student_id}&limit=100",
        )
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert sa_com_rid in ids, "Pending SA_COM должен быть в списке"
        assert sa_rid not in ids, (
            f"Auto-checked SA rid={sa_rid} не должен быть в pending review list"
        )
    finally:
        await _cleanup(
            db, student_id=student_id,
            task_ids=[sa_com_task, sa_task], rids=[sa_com_rid, sa_rid],
        )
