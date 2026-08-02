"""tsk-372: GET /teacher/reviews/pending?review_kind=mandatory|optional|all.

Портал преподавателя (SPW) закрывает разрыв с ТГ-ботом: бот уже показывал
опциональную очередь (`/task-results/by-pending-review?review_kind=optional`,
tsk-230) через свой отдельный эндпоинт; портал был жёстко привязан к
mandatory. Default (mandatory) обязан остаться идентичным поведению до
tsk-372 — фикстуры и сценарии зеркалят test_teacher_reviews_pending_tsk298.py.
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


async def _setup_methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"t372-met-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t372-met", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"t372-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t372-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db, *, type_: str = "SA_COM", manual: bool = True) -> int:
    rules: dict = {"max_score": 10, "manual_review_required": manual}
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"t372-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "rules": json.dumps(rules),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


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


async def _cleanup(db, *, user_ids, task_ids=(), rids=()):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": list(rids)})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": list(task_ids)})
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


@pytest.mark.asyncio
async def test_pending_default_review_kind_unchanged(db, client):
    """Без review_kind — как раньше: только mandatory (tsk-298 default)."""
    met_id, token = await _setup_methodist(db)
    stud_id = await _create_student(db)
    mandatory_task = await _create_task(db, manual=True)
    mandatory_rid = await _create_tr(db, user_id=stud_id, task_id=mandatory_task, is_correct=None)
    optional_task = await _create_task(db, manual=False)
    optional_rid = await _create_tr(db, user_id=stud_id, task_id=optional_task, is_correct=False)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert mandatory_rid in ids
        assert optional_rid not in ids
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[mandatory_task, optional_task],
                       rids=[mandatory_rid, optional_rid])


@pytest.mark.asyncio
async def test_pending_review_kind_optional_returns_auto_checked(db, client):
    """review_kind=optional отдаёт авто-проверенный SA_COM (включая честно-заваленный)."""
    met_id, token = await _setup_methodist(db)
    stud_id = await _create_student(db)
    mandatory_task = await _create_task(db, manual=True)
    mandatory_rid = await _create_tr(db, user_id=stud_id, task_id=mandatory_task, is_correct=None)
    optional_task = await _create_task(db, manual=False)
    optional_rid = await _create_tr(db, user_id=stud_id, task_id=optional_task, is_correct=False)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&review_kind=optional",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [it["id"] for it in body["items"]]
        assert optional_rid in ids, "авто-проверенный SA_COM должен быть в optional-очереди"
        assert mandatory_rid not in ids, "обязательная работа не должна попадать в optional"
        mine = next(it for it in body["items"] if it["id"] == optional_rid)
        assert mine["is_correct"] is False
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[mandatory_task, optional_task],
                       rids=[mandatory_rid, optional_rid])


@pytest.mark.asyncio
async def test_pending_review_kind_all_returns_both(db, client):
    """review_kind=all — объединение mandatory и optional очередей."""
    met_id, token = await _setup_methodist(db)
    stud_id = await _create_student(db)
    mandatory_task = await _create_task(db, manual=True)
    mandatory_rid = await _create_tr(db, user_id=stud_id, task_id=mandatory_task, is_correct=None)
    optional_task = await _create_task(db, manual=False)
    optional_rid = await _create_tr(db, user_id=stud_id, task_id=optional_task, is_correct=True)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&review_kind=all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert mandatory_rid in ids
        assert optional_rid in ids
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[mandatory_task, optional_task],
                       rids=[mandatory_rid, optional_rid])


@pytest.mark.asyncio
async def test_pending_review_kind_optional_excludes_not_yet_auto_checked(db, client):
    """optional требует is_correct задан — SA_COM без вердикта (is_correct NULL) не попадает."""
    met_id, token = await _setup_methodist(db)
    stud_id = await _create_student(db)
    # SA_COM с manual_review_required=false, но is_correct ещё NULL (гипотетический
    # промежуточный статус — авто-чек не отработал) не должен попасть ни в одну очередь.
    task_id = await _create_task(db, manual=False)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, is_correct=None)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&review_kind=optional",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert rid not in ids
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[task_id], rids=[rid])
