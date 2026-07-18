"""tsk-298 Фаза 2 (LMS enablers): teacher-scoped очередь `GET /teacher/reviews/pending`,
`attempt_id` в claim-item, расширение ACL скачивания вложения на препода-ревьюера.

Фикстуры — как в test_review_queue_alignment_tsk247.py (methodist-bypass в
REVIEW_ACL упрощает setup без teacher_courses).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import teacher_queue_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"t298p-met-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t298p-met", tg_id=None,
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


async def _create_user_with_session(db, tag: str) -> tuple[int, str]:
    u = Users(
        email=f"t298p-{tag}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"t298p-{tag}", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _create_task(db, *, type_: str = "SA_COM", manual: bool = True) -> int:
    rules: dict = {"max_score": 10, "manual_review_required": manual}
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"t298p-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "rules": json.dumps(rules),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_attempt(db, user_id: int) -> int:
    res = await db.execute(
        text("INSERT INTO attempts (user_id) VALUES (:u) RETURNING id"),
        {"u": user_id},
    )
    aid = res.scalar_one()
    await db.commit()
    return aid


async def _create_tr(db, *, user_id: int, task_id: int, attempt_id: int | None,
                     is_correct: bool | None = None, score: int = 0) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, attempt_id, submitted_at, "
            "count_retry, received_at, max_score, source_system, is_correct) "
            "VALUES (:s, :u, :t, :a, :now, 0, :now, 10, 'spw', :ic) RETURNING id"
        ),
        {"s": score, "u": user_id, "t": task_id, "a": attempt_id, "now": now, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, user_ids, task_ids=(), rids=(), attempt_ids=()):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": list(rids)})
    if attempt_ids:
        await db.execute(text("DELETE FROM attempts WHERE id = ANY(:a)"), {"a": list(attempt_ids)})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": list(task_ids)})
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


# ── GET /teacher/reviews/pending ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_list_shows_item_with_attempt_id(db, client):
    """Список отдаёт ожидающую работу с attempt_id и лёгким контекстом."""
    met_id, token = await _setup_methodist(db)
    stud_id, _ = await _create_user_with_session(db, "stud")
    task_id = await _create_task(db, manual=True)
    aid = await _create_attempt(db, stud_id)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, attempt_id=aid, is_correct=None)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] >= 1
        mine = next((it for it in body["items"] if it["id"] == rid), None)
        assert mine is not None, "созданная работа должна быть в очереди"
        assert mine["attempt_id"] == aid
        assert mine["user_id"] == stud_id
        assert mine["max_score"] == 10
        assert mine["is_claimed"] is False
        assert "answer_json" not in mine  # лёгкий item без тяжёлого ответа
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[task_id],
                       rids=[rid], attempt_ids=[aid])


@pytest.mark.asyncio
async def test_pending_list_marks_claimed(db, client):
    """Работа с действующим lock помечается is_claimed=true."""
    met_id, token = await _setup_methodist(db)
    stud_id, _ = await _create_user_with_session(db, "stud")
    task_id = await _create_task(db, manual=True)
    aid = await _create_attempt(db, stud_id)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, attempt_id=aid, is_correct=None)
    try:
        await db.execute(
            text(
                "UPDATE task_results SET review_claimed_by=:o, review_claim_token='x', "
                "review_claim_expires_at = now() + interval '10 minutes' WHERE id=:r"
            ),
            {"o": met_id, "r": rid},
        )
        await db.commit()
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        mine = next((it for it in resp.json()["items"] if it["id"] == rid), None)
        assert mine is not None and mine["is_claimed"] is True
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[task_id],
                       rids=[rid], attempt_ids=[aid])


@pytest.mark.asyncio
async def test_pending_list_forbidden_wrong_teacher(db, client):
    """Запрос очереди с чужим teacher_id → 403 (identity-гейт)."""
    met_id, token = await _setup_methodist(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id + 999999}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[met_id])


# ── attempt_id в claim-next ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_next_item_includes_attempt_id(db, client):
    """claim-next отдаёт attempt_id в item (нужен вебу для URL вложений)."""
    met_id, token = await _setup_methodist(db)
    stud_id, _ = await _create_user_with_session(db, "stud")
    task_id = await _create_task(db, manual=True)
    aid = await _create_attempt(db, stud_id)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, attempt_id=aid, is_correct=None)
    try:
        resp = await client.post(
            "/api/v1/teacher/reviews/claim-next",
            json={"teacher_id": met_id, "user_id": stud_id, "ttl_sec": 120},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["empty"] is False
        assert body["item"]["id"] == rid
        assert body["item"]["attempt_id"] == aid
    finally:
        await _cleanup(db, user_ids=[met_id, stud_id], task_ids=[task_id],
                       rids=[rid], attempt_ids=[aid])


# ── ACL вложения ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_attachment_acl_service_helper(db):
    """teacher_can_review_attempt: methodist (bypass) → true, посторонний → false."""
    met_id, _ = await _setup_methodist(db)
    other_id, _ = await _create_user_with_session(db, "other")
    stud_id, _ = await _create_user_with_session(db, "stud")
    task_id = await _create_task(db, manual=True)
    aid = await _create_attempt(db, stud_id)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, attempt_id=aid, is_correct=None)
    try:
        assert await teacher_queue_service.teacher_can_review_attempt(db, aid, met_id) is True
        assert await teacher_queue_service.teacher_can_review_attempt(db, aid, other_id) is False
    finally:
        await _cleanup(db, user_ids=[met_id, other_id, stud_id], task_ids=[task_id],
                       rids=[rid], attempt_ids=[aid])


@pytest.mark.asyncio
async def test_attachment_endpoint_extends_acl_to_reviewer(db, client):
    """Download-эндпоинт: посторонний → 403; авторизованный препод проходит ACL
    (файла нет → 404, но НЕ 403 — значит ACL расширен)."""
    met_id, met_token = await _setup_methodist(db)
    other_id, other_token = await _create_user_with_session(db, "other")
    stud_id, _ = await _create_user_with_session(db, "stud")
    task_id = await _create_task(db, manual=True)
    aid = await _create_attempt(db, stud_id)
    rid = await _create_tr(db, user_id=stud_id, task_id=task_id, attempt_id=aid, is_correct=None)
    try:
        # Посторонний (не владелец, без ACL) → 403.
        r_other = await client.get(
            f"/api/v1/attempts/{aid}/attachments/att-does-not-exist",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert r_other.status_code == 403, r_other.text
        # Методист (авторизован на проверку) → ACL пройден, файла нет → 404 (не 403).
        r_met = await client.get(
            f"/api/v1/attempts/{aid}/attachments/att-does-not-exist",
            headers={"Authorization": f"Bearer {met_token}"},
        )
        assert r_met.status_code != 403, r_met.text
    finally:
        await _cleanup(db, user_ids=[met_id, other_id, stud_id], task_ids=[task_id],
                       rids=[rid], attempt_ids=[aid])
