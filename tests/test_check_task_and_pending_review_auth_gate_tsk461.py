# -*- coding: utf-8 -*-
"""
tsk-461: регресс-тесты на закрытые гейты авторизации.

1. POST /api/v1/check/task и /check/tasks-batch — были вообще без Depends
   (открыты в интернет без единого гейта). Закрыты `get_current_user`
   (cookie/Bearer ИЛИ сервисный ключ).
2. GET /api/v1/task-results/by-pending-review — висел на legacy `get_db`
   (только `?api_key=`, без CurrentUser). Переведён на
   `require_role("teacher","methodist","admin")` (сервисный ключ — bypass,
   чтобы не сломать TG_LMS bot-поллеры).

Третий пункт задачи (`/courses/by-code`) оставлен без изменений по решению
оператора — см. docs/ai/adr/0004-courses-by-code-public-resolver.md.
"""
import os
import random
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


def _sa_check_payload() -> dict:
    """Минимальный валидный запрос на проверку задачи типа SA (короткий ответ)."""
    return {
        "task_content": {
            "type": "SA",
            "stem": "Сколько будет 2+2?",
        },
        "solution_rules": {
            "max_score": 10,
            "short_answer": {
                "accepted_answers": [{"value": "4", "score": 10}],
            },
        },
        "answer": {
            "type": "SA",
            "response": {"value": "4"},
        },
    }


async def _setup_user_with_roles(db, roles: list[str]) -> tuple[int, str]:
    """Создать user + email-identity + роли + session. Возврат (user_id, token)."""
    email = f"tsk461_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name=None, tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    for role_name in roles:
        role_id = (
            await db.execute(
                text("SELECT id FROM roles WHERE name = :n"), {"n": role_name}
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                "ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "r": role_id},
        )
    access_token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, access_token


async def _cleanup(db, user_id: int) -> None:
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── /check/task, /check/tasks-batch ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_task_requires_auth(client):
    """Без единого источника аутентификации — 401 (было: 200 всем подряд)."""
    resp = await client.post("/api/v1/check/task", json=_sa_check_payload())
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_check_tasks_batch_requires_auth(client):
    resp = await client.post(
        "/api/v1/check/tasks-batch", json={"items": [_sa_check_payload()]}
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_check_task_service_key_ok(client):
    """Сервисный X-API-Key (TG_LMS боты) — доступ сохранён после закрытия гейта."""
    api_key = next(iter(_settings.valid_api_keys))
    resp = await client.post(
        "/api/v1/check/task",
        json=_sa_check_payload(),
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_correct"] is True


@pytest.mark.asyncio
async def test_check_task_session_cookie_ok(db, client):
    """Обычный аутентифицированный пользователь (cookie ученика) — доступ есть."""
    user_id, token = await _setup_user_with_roles(db, ["student"])
    try:
        resp = await client.post(
            "/api/v1/check/task",
            json=_sa_check_payload(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_correct"] is True
    finally:
        await _cleanup(db, user_id)


# ── /task-results/by-pending-review ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_review_forbidden_for_student(db, client):
    """Аутентифицированный, но без роли teacher/methodist/admin — 403."""
    user_id, token = await _setup_user_with_roles(db, ["student"])
    try:
        resp = await client.get(
            "/api/v1/task-results/by-pending-review",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_pending_review_ok_for_teacher(db, client):
    user_id, token = await _setup_user_with_roles(db, ["teacher"])
    try:
        resp = await client.get(
            "/api/v1/task-results/by-pending-review",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_pending_review_service_key_bypass(client):
    """Сервисный ключ (TG_LMS teacher/methodist bot-поллеры) — bypass, не сломан."""
    api_key = next(iter(_settings.valid_api_keys))
    resp = await client.get(
        "/api/v1/task-results/by-pending-review",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_pending_review_requires_auth(client):
    """Без единого источника аутентификации — 401."""
    resp = await client.get("/api/v1/task-results/by-pending-review")
    assert resp.status_code == 401, resp.text
