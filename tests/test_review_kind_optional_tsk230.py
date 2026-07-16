"""tsk-230 follow-up: GET /by-pending-review?review_kind=optional.

optional = авто-проверенные SA_COM (checked_at IS NULL, is_correct задан,
manual_review_required=false) — для опционального просмотра преподавателем.
mandatory (default) их НЕ возвращает (там is_correct IS NULL).
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
    key = os.environ.get("VALID_API_KEYS", "").split(",")[0].strip()
    if not key:
        pytest.skip("VALID_API_KEYS не задан в .env — пропускаем")
    return f"api_key={key}"


async def _create_task(db, *, type_: str, manual: bool | None) -> int:
    content = {"type": type_, "stem": "test"}
    rules: dict = {"max_score": 10}
    if manual is not None:
        rules["manual_review_required"] = manual
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"rkopt-{random.randint(10**8, 10**10)}",
            "content": json.dumps(content),
            "rules": json.dumps(rules),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_student(db) -> int:
    u = Users(email=f"rkopt-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name="rkopt", tg_id=None)
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
async def test_optional_returns_auto_checked_sa_com(db, client):
    """review_kind=optional возвращает авто-проверенный SA_COM (is_correct задан)."""
    qs = _api_key_qs()
    student_id = await _create_student(db)
    auto_task = await _create_task(db, type_="SA_COM", manual=False)
    auto_rid = await _create_tr(db, user_id=student_id, task_id=auto_task, is_correct=False)
    pending_task = await _create_task(db, type_="SA_COM", manual=True)
    pending_rid = await _create_tr(db, user_id=student_id, task_id=pending_task, is_correct=None)
    try:
        resp = await client.get(
            f"/api/v1/task-results/by-pending-review?{qs}&user_id={student_id}&review_kind=optional&limit=100"
        )
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert auto_rid in ids, "Авто-проверенный SA_COM должен быть в optional-списке"
        assert pending_rid not in ids, "Обязательно-ожидающий (is_correct NULL) не в optional"
    finally:
        await _cleanup(db, student_id=student_id,
                       task_ids=[auto_task, pending_task], rids=[auto_rid, pending_rid])


@pytest.mark.asyncio
async def test_mandatory_default_excludes_auto_checked(db, client):
    """review_kind по умолчанию (mandatory) НЕ возвращает авто-проверенный SA_COM."""
    qs = _api_key_qs()
    student_id = await _create_student(db)
    auto_task = await _create_task(db, type_="SA_COM", manual=False)
    auto_rid = await _create_tr(db, user_id=student_id, task_id=auto_task, is_correct=True)
    try:
        resp = await client.get(
            f"/api/v1/task-results/by-pending-review?{qs}&user_id={student_id}&limit=100"
        )
        assert resp.status_code == 200, resp.text
        ids = [r["id"] for r in resp.json()]
        assert auto_rid not in ids, "Авто-проверенный SA_COM не должен быть в mandatory-очереди"
    finally:
        await _cleanup(db, student_id=student_id, task_ids=[auto_task], rids=[auto_rid])
