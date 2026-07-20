"""tsk-298 Фаза 3-Ⅱ: help-requests + override открыты cookie-преподавателю.

- help-requests list: cookie своего teacher_id → 200, чужого → 403, service → 200.
- task-limits/override: identity по updated_by + ACL (course-tree/свой ученик/methodist):
  methodist → 200, чужой updated_by → 403, teacher без ACL → 403, service → 200.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t298h-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t298h", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _student(db) -> int:
    u = Users(email=f"t298h-stu-{random.randint(10**8, 10**10)}@example.com",
              password_hash=None, full_name="t298h-stu", tg_id=None)
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _task(db, *, task_type: str = "SA_COM", max_attempts: int | None = None) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, max_attempts) "
            "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), 1, 1, :ma) RETURNING id"
        ),
        {"e": f"t298h-{random.randint(10**8, 10**10)}",
         "c": json.dumps({"type": task_type, "stem": "x"}),
         "r": json.dumps({"max_score": 10}),
         "ma": max_attempts},
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _cleanup(db, *, user_ids, task_ids=()):
    for uid in user_ids:
        await db.execute(text("DELETE FROM student_task_limit_override WHERE student_id=:u OR updated_by=:u"), {"u": uid})
        await db.execute(text("DELETE FROM student_teacher_links WHERE teacher_id=:u OR student_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    for tid in task_ids:
        await db.execute(text("DELETE FROM student_task_limit_override WHERE task_id=:t"), {"t": tid})
        await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": tid})
    await db.commit()


# ── help-requests list ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_help_requests_list_cookie_self(db, client):
    tid, token = await _user_with_session(db, "teacher")
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()
    finally:
        await _cleanup(db, user_ids=[tid])


@pytest.mark.asyncio
async def test_help_requests_list_cookie_foreign_forbidden(db, client):
    tid, token = await _user_with_session(db, "teacher")
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={tid + 987654}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[tid])


@pytest.mark.asyncio
async def test_help_requests_list_service_bypass(db, client):
    tid, _ = await _user_with_session(db, "teacher")
    api_key = next(iter(_settings.valid_api_keys))
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={tid}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[tid])


# ── override ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_help_request_detail_student_name_is_full_name(db, client):
    """Regression off-by-one: деталь возвращает ФИО ученика в student_name,
    а НЕ external_uid задания (маппинг dict был сдвинут на +1)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)  # full_name = 't298h-stu'
    tid = await _task(db)
    r = await db.execute(
        text(
            "INSERT INTO help_requests (status, student_id, task_id, request_type, "
            "auto_created, context_json, priority, created_at, updated_at) "
            "VALUES ('open', :s, :t, 'blocked_limit', false, '{}'::jsonb, 100, now(), now()) RETURNING id"
        ),
        {"s": sid, "t": tid},
    )
    hr_id = r.scalar_one()
    await db.commit()
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests/{hr_id}?teacher_id={mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["student_name"] == "t298h-stu", body
        assert body["student_name"] != body.get("task_title")
    finally:
        await db.execute(text("DELETE FROM help_requests WHERE id=:h"), {"h": hr_id})
        await db.commit()
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_methodist_allowed(db, client):
    """Методист (ACL bypass) может переопределить лимит."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "reason": "тест", "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_foreign_updated_by_forbidden(db, client):
    """updated_by != current_user → 403 (нельзя действовать за другого)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "reason": "тест", "updated_by": mid + 55555},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_teacher_without_acl_forbidden(db, client):
    """Teacher без ACL (не свой ученик, не course-tree, не methodist) → 403."""
    tid_user, token = await _user_with_session(db, "teacher")
    sid = await _student(db)
    tid = await _task(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "reason": "тест", "updated_by": tid_user},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_ids=[tid_user, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_teacher_linked_student_allowed(db, client):
    """Teacher со связью student_teacher_links → 200."""
    tid_user, token = await _user_with_session(db, "teacher")
    sid = await _student(db)
    tid = await _task(db)
    await db.execute(
        text("INSERT INTO student_teacher_links (student_id, teacher_id) VALUES (:s, :t) ON CONFLICT DO NOTHING"),
        {"s": sid, "t": tid_user},
    )
    await db.commit()
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "reason": "тест", "updated_by": tid_user},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[tid_user, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_service_bypass(db, client):
    sid = await _student(db)
    tid = await _task(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "reason": "тест", "updated_by": sid},
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db, user_ids=[sid], task_ids=[tid])


# ── tsk-335: grant_same_again (выдать столько же, сколько было) ─────────────

@pytest.mark.asyncio
async def test_override_grant_same_again_no_prior_override(db, client):
    """Без override и без task.max_attempts: база = DEFAULT_MAX_ATTEMPTS=3,
    первый вызов поднимает эффективный лимит 3 -> 6 (текущий=база, +база)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db)  # max_attempts=NULL -> DEFAULT_MAX_ATTEMPTS
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["previous_max_attempts_override"] is None
        assert body["base_attempts_added"] == 3
        assert body["max_attempts_override"] == 6
        assert body["already"] is False
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_grant_same_again_uses_task_base_limit(db, client):
    """task.max_attempts=5 -> база=5; повторный вызов поднимает ЕЩЁ на 5 от
    текущего эффективного (не от базы), т.е. 5 -> 10 -> 15."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db, max_attempts=5)
    try:
        r1 = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["max_attempts_override"] == 10
        assert r1.json()["base_attempts_added"] == 5

        # Второй вызов вне окна дебаунса (сбрасываем updated_at override в прошлое,
        # чтобы не зависеть от реального времени ожидания в тесте).
        await db.execute(
            text(
                "UPDATE student_task_limit_override SET updated_at = now() - interval '10 seconds' "
                "WHERE student_id=:s AND task_id=:t"
            ),
            {"s": sid, "t": tid},
        )
        await db.commit()

        r2 = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["previous_max_attempts_override"] == 10
        assert r2.json()["max_attempts_override"] == 15
        assert r2.json()["base_attempts_added"] == 5
        assert r2.json()["already"] is False
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_grant_same_again_debounced_double_click(db, client):
    """Двойной клик в окне дебаунса тем же оператором -> already=true, лимит
    не удваивается (защита от накрутки, tsk-335 п.3)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db, max_attempts=3)
    try:
        r1 = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r1.status_code == 200, r1.text
        limit_after_first = r1.json()["max_attempts_override"]

        r2 = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["already"] is True
        assert r2.json()["max_attempts_override"] == limit_after_first

        row = (await db.execute(
            text("SELECT max_attempts_override FROM student_task_limit_override "
                 "WHERE student_id=:s AND task_id=:t"),
            {"s": sid, "t": tid},
        )).fetchone()
        assert int(row[0]) == limit_after_first
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_quiz_rejected_explicit(db, client):
    """Квиз-вопрос всегда ограничен 1 попыткой — explicit override -> 422
    (раньше тихо принимался и не действовал; закрываем как побочный фикс)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db, task_type="SC_Qw")
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "max_attempts_override": 5,
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_quiz_rejected_grant_same_again(db, client):
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db, task_type="MC_Qw")
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_explicit_requires_max_attempts(db, client):
    """mode=explicit без max_attempts_override -> 422 (валидация схемы)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "explicit", "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])


@pytest.mark.asyncio
async def test_override_grant_same_again_forbids_explicit_number(db, client):
    """mode=grant_same_again с явным max_attempts_override -> 422 (два способа
    задать лимит одним вызовом запрещены, tsk-335 п.6)."""
    mid, token = await _user_with_session(db, "methodist")
    sid = await _student(db)
    tid = await _task(db)
    try:
        resp = await client.post(
            "/api/v1/teacher/task-limits/override",
            json={"student_id": sid, "task_id": tid, "mode": "grant_same_again",
                  "max_attempts_override": 5, "updated_by": mid},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[mid, sid], task_ids=[tid])
