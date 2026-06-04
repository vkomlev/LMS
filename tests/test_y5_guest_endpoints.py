"""Tests Phase Y-5: guest endpoints + embed-api + attribute-guest.

Покрывает tech-spec Y-5 §6.7.3 acceptance criteria.
Использует существующий public-demo курс из S6 seed
(course_uid='wp:rabota-so-strokami-v-python', task_id=151 type=SC,
correct option_id='A').
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text


_DEMO_COURSE_UID = "pytest:y5-public-demo"
_DEMO_TASK_ID_SC = 0
_DEMO_TASK_EXTERNAL_UID_SC = "pytest:y5-public-demo:sc"
_CORRECT_OPTION_ID = "A"
_INCORRECT_OPTION_ID = "B"


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_y5_rate_limit_keys():
    """Очищает rate-limit Redis-ключи Y-5 перед каждым тестом — без этого
    повторный запуск встречает 429.

    Используем свежий Redis-клиент на каждый тест: глобальный pool из
    rate_limit_service может застрять на event-loop предыдущего теста
    (см. conftest NullPool обоснование).
    """
    import os
    import redis.asyncio as aioredis

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis = aioredis.from_url(redis_url, decode_responses=True)
    patterns = [
        "guest_session:*",
        "guest_read:*",
        "guest_attempt:*",
        "guest_attempt_session:*",
        "embed_issue:*",
        "embed_read:*",
        "attribute_guest:*",
        "embed_jti:*",
    ]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _seed_y5_public_demo(db):
    """Создать автономный demo-seed вместо зависимости от наполнения dev-БД."""
    global _DEMO_TASK_ID_SC

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                "VALUES ('pytest Y5 public demo', 'self_guided', :uid, TRUE) "
                "RETURNING id"
            ),
            {"uid": _DEMO_COURSE_UID},
        )
    ).scalar_one()
    task_content = {
        "type": "SC",
        "stem": "Выберите правильный вариант.",
        "options": [
            {"id": _CORRECT_OPTION_ID, "text": "Правильный вариант"},
            {"id": _INCORRECT_OPTION_ID, "text": "Неправильный вариант"},
        ],
    }
    solution_rules = {
        "max_score": 1,
        "correct_options": [_CORRECT_OPTION_ID],
    }
    _DEMO_TASK_ID_SC = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks "
                    "(external_uid, max_score, task_content, course_id, difficulty_id, solution_rules) "
                    "VALUES (:uid, 1, CAST(:task_content AS jsonb), :course_id, :difficulty_id, "
                    "CAST(:solution_rules AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "uid": _DEMO_TASK_EXTERNAL_UID_SC,
                    "task_content": json.dumps(task_content, ensure_ascii=False),
                    "course_id": course_id,
                    "difficulty_id": difficulty_id,
                    "solution_rules": json.dumps(solution_rules),
                },
            )
        ).scalar_one()
    )
    await db.commit()
    try:
        yield
    finally:
        await db.execute(
            text("DELETE FROM courses WHERE course_uid = :uid"),
            {"uid": _DEMO_COURSE_UID},
        )
        await db.commit()


# ─── helpers ────────────────────────────────────────────────────────────────

async def _create_temp_user(db) -> int:
    """Создать временного пользователя для attribute-guest тестов."""
    suffix = uuid4().hex[:10]
    res = await db.execute(
        text("INSERT INTO users (email) VALUES (:e) RETURNING id"),
        {"e": f"y5-test-{suffix}@example.test"},
    )
    user_id = int(res.scalar_one())
    await db.commit()
    return user_id


# ─── §6.2.1: POST /learning/guest/session ───────────────────────────────────

@pytest.mark.asyncio
async def test_create_guest_session_returns_uuid_and_cookie(client):
    """POST /learning/guest/session → 201 + uuid + cookie set."""
    resp = await client.post("/api/v1/learning/guest/session")
    assert resp.status_code == 201
    body = resp.json()
    assert "guest_session_id" in body
    assert "expires_at" in body
    assert len(body["guest_session_id"]) == 36
    # Cookie должен быть установлен (имя guest_session)
    assert "guest_session" in resp.cookies


# ─── §6.2.2: GET /learning/guest/courses/{course_uid} ───────────────────────

@pytest.mark.asyncio
async def test_get_guest_course_info_200_for_demo(client):
    resp = await client.get(f"/api/v1/learning/guest/courses/{_DEMO_COURSE_UID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["course_uid"] == _DEMO_COURSE_UID
    assert body["is_public_demo"] is True
    assert body["title"]


@pytest.mark.asyncio
async def test_get_guest_course_info_404_not_demo(client):
    """course_uid существующего НЕ-demo курса → 404."""
    resp = await client.get("/api/v1/learning/guest/courses/PY")
    assert resp.status_code == 404


# ─── §6.2.2: GET /learning/guest/task/{task_id} ─────────────────────────────

@pytest.mark.asyncio
async def test_get_guest_task_200_no_correct_answer_in_payload(client):
    resp = await client.get(f"/api/v1/learning/guest/task/{_DEMO_TASK_ID_SC}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == _DEMO_TASK_ID_SC
    assert body["type"] in ("SA", "SC", "MC")
    assert body["stem"]
    # Защита от слива:
    assert "correct_answer" not in body
    assert "solution_rules" not in body
    assert "correct_options" not in body
    if body.get("options"):
        for opt in body["options"]:
            assert "is_correct" not in opt
            # whitelist: только id и text
            assert set(opt.keys()) == {"id", "text"}


@pytest.mark.asyncio
async def test_get_guest_task_404_not_in_demo(client, db):
    """task существующий, но в non-demo курсе → 404."""
    res = await db.execute(
        text(
            """
            SELECT t.id FROM tasks t
            JOIN courses c ON c.id=t.course_id
            WHERE c.is_public_demo=FALSE AND t.external_uid IS NOT NULL
            LIMIT 1
            """
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        pytest.skip("Нет non-demo задач в БД для отрицательного теста")
    resp = await client.get(f"/api/v1/learning/guest/task/{row}")
    assert resp.status_code == 404


# ─── §6.2.3: POST /learning/guest/attempts ──────────────────────────────────

@pytest.mark.asyncio
async def test_post_guest_attempt_400_without_cookie(client):
    resp = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": _DEMO_TASK_ID_SC,
            "answer": {"type": "SC", "response": {"selected_option_ids": [_CORRECT_OPTION_ID]}},
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_guest_attempt_correct_answer(client):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201
    # cookie уже сохранён в client автоматически

    resp = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": _DEMO_TASK_ID_SC,
            "answer": {"type": "SC", "response": {"selected_option_ids": [_CORRECT_OPTION_ID]}},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_correct"] is True
    assert body["score"] >= 1
    assert body["max_score"] >= 1
    assert "attempt_id" in body


@pytest.mark.asyncio
async def test_post_guest_attempt_incorrect_answer(client):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    resp = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": _DEMO_TASK_ID_SC,
            "answer": {"type": "SC", "response": {"selected_option_ids": [_INCORRECT_OPTION_ID]}},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["is_correct"] is False


@pytest.mark.asyncio
async def test_post_guest_attempt_400_for_non_demo_task(client, db):
    res = await db.execute(
        text(
            """
            SELECT t.id FROM tasks t
            JOIN courses c ON c.id=t.course_id
            WHERE c.is_public_demo=FALSE
              AND COALESCE(t.task_content->>'type','SA') IN ('SA','SC','MC')
            LIMIT 1
            """
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        pytest.skip("Нет non-demo SA/SC/MC задач")

    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201
    resp = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": int(row),
            "answer": {"type": "SC", "response": {"selected_option_ids": ["A"]}},
        },
    )
    # ACL не пускает: 404 (учитывается по tech-spec §6.2.3)
    assert resp.status_code in (400, 404)


# ─── §6.2.4: POST /me/attribute-guest ───────────────────────────────────────

@pytest.mark.asyncio
async def test_attribute_guest_post_login_first_call_attributes_attempts(db):
    """Прямой вызов сервисного метода — атрибуция unattributed attempt'ов."""
    from app.models.guest_attempt import GuestAttempt
    from app.models.guest_session import GuestSession
    from app.services.auth.guest_attribution_service import (
        attribute_guest_post_login,
    )

    user_id = await _create_temp_user(db)

    gs = GuestSession(ip="127.0.0.1")
    db.add(gs)
    await db.flush()
    ga = GuestAttempt(guest_session_id=gs.id, task_id=None, answer_json={})
    db.add(ga)
    await db.commit()

    result = await attribute_guest_post_login(db, user_id=user_id, guest_session_id=gs.id)
    await db.commit()

    assert result.found is True
    assert result.already_attributed is False
    assert result.attributed_count == 1

    await db.refresh(ga)
    assert ga.attributed_user_id == user_id
    assert ga.attributed_at is not None


@pytest.mark.asyncio
async def test_attribute_guest_post_login_idempotent(db):
    from app.models.guest_attempt import GuestAttempt
    from app.models.guest_session import GuestSession
    from app.services.auth.guest_attribution_service import (
        attribute_guest_post_login,
    )

    user_id = await _create_temp_user(db)

    gs = GuestSession(ip="127.0.0.1")
    db.add(gs)
    await db.flush()
    ga = GuestAttempt(guest_session_id=gs.id, task_id=None, answer_json={})
    db.add(ga)
    await db.commit()

    await attribute_guest_post_login(db, user_id=user_id, guest_session_id=gs.id)
    await db.commit()

    # Повторный вызов
    result2 = await attribute_guest_post_login(db, user_id=user_id, guest_session_id=gs.id)
    await db.commit()

    assert result2.found is True
    assert result2.already_attributed is True
    assert result2.attributed_count == 0


@pytest.mark.asyncio
async def test_attribute_guest_post_login_409_other_user(db):
    from app.models.guest_session import GuestSession
    from app.services.auth.guest_attribution_service import (
        GuestAttributionConflictError,
        attribute_guest_post_login,
    )

    user_a = await _create_temp_user(db)
    user_b = await _create_temp_user(db)

    gs = GuestSession(ip="127.0.0.1")
    db.add(gs)
    await db.commit()

    await attribute_guest_post_login(db, user_id=user_a, guest_session_id=gs.id)
    await db.commit()

    with pytest.raises(GuestAttributionConflictError):
        await attribute_guest_post_login(db, user_id=user_b, guest_session_id=gs.id)


@pytest.mark.asyncio
async def test_attribute_guest_post_login_404_unknown_session(db):
    from app.services.auth.guest_attribution_service import (
        attribute_guest_post_login,
    )

    user_id = await _create_temp_user(db)
    result = await attribute_guest_post_login(
        db, user_id=user_id, guest_session_id=uuid4()
    )
    assert result.found is False
    assert result.attributed_count == 0


# ─── §6.3: embed-api ────────────────────────────────────────────────────────

_TEST_EMBED_SECRET = "test-secret-y5-" + "x" * 32


@pytest.fixture
def embed_secret_set(monkeypatch):
    """Подменяет _settings.embed_jwt_secret в embed-api модулях."""
    monkeypatch.setenv("CB_EMBED_JWT_SECRET", _TEST_EMBED_SECRET)
    from app.api.v1 import embed_api as embed_module

    monkeypatch.setattr(embed_module._settings, "embed_jwt_secret", _TEST_EMBED_SECRET)
    monkeypatch.setattr(embed_module._settings, "embed_jwt_ttl_sec", 300)
    yield


@pytest.mark.asyncio
async def test_embed_issue_404_not_in_demo(client, embed_secret_set):
    resp = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": "PY", "external_uid": "TASK-SC-001"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_embed_issue_and_consume_payload_no_correct_answer(client, embed_secret_set):
    issue = await client.post(
        "/embed-api/auth/issue",
        json={
            "course_uid": _DEMO_COURSE_UID,
            "external_uid": _DEMO_TASK_EXTERNAL_UID_SC,
        },
    )
    assert issue.status_code == 200, issue.text
    issued = issue.json()
    token = issued["token"]
    assert token

    read = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_DEMO_TASK_EXTERNAL_UID_SC}",
        params={"token": token},
    )
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["task_id"] == _DEMO_TASK_ID_SC
    assert body["type"] in ("SA", "SC", "MC")
    assert body["stem"]
    assert body["deeplink_url"].startswith(("http://", "https://"))
    assert "utm_source=wp-embed" in body["deeplink_url"]
    # Sanitization
    assert "correct_answer" not in body
    assert "solution_rules" not in body
    assert "correct_options" not in body
    if body.get("options"):
        for opt in body["options"]:
            assert "is_correct" not in opt
            assert set(opt.keys()) == {"id", "label"}


@pytest.mark.asyncio
async def test_embed_token_single_use_second_read_returns_401(client, embed_secret_set):
    issue = await client.post(
        "/embed-api/auth/issue",
        json={
            "course_uid": _DEMO_COURSE_UID,
            "external_uid": _DEMO_TASK_EXTERNAL_UID_SC,
        },
    )
    token = issue.json()["token"]

    first = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_DEMO_TASK_EXTERNAL_UID_SC}",
        params={"token": token},
    )
    assert first.status_code == 200

    second = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_DEMO_TASK_EXTERNAL_UID_SC}",
        params={"token": token},
    )
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_embed_consume_invalid_token_returns_401(client, embed_secret_set):
    resp = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_DEMO_TASK_EXTERNAL_UID_SC}",
        params={"token": "not.a.valid.jwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_embed_consume_token_for_different_task_returns_401(client, embed_secret_set, db):
    """Token, выпущенный для другой задачи, не должен валидироваться против чужого URL."""
    res = await db.execute(
        text(
            """
            SELECT t.external_uid FROM tasks t
            JOIN courses c ON c.id=t.course_id
            WHERE c.course_uid=:cu AND t.external_uid != :exclude
              AND t.external_uid IS NOT NULL
            LIMIT 1
            """
        ),
        {"cu": _DEMO_COURSE_UID, "exclude": _DEMO_TASK_EXTERNAL_UID_SC},
    )
    other_uid = res.scalar_one_or_none()
    if other_uid is None:
        pytest.skip("Нет второй demo-задачи")

    issue = await client.post(
        "/embed-api/auth/issue",
        json={
            "course_uid": _DEMO_COURSE_UID,
            "external_uid": _DEMO_TASK_EXTERNAL_UID_SC,
        },
    )
    token = issue.json()["token"]
    # пробуем прочитать другой URL с этим токеном
    resp = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{other_uid}",
        params={"token": token},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_embed_issue_503_when_secret_missing(client, monkeypatch):
    """Fail-secure: без CB_EMBED_JWT_SECRET issue возвращает 503."""
    from app.api.v1 import embed_api as embed_module

    monkeypatch.setattr(embed_module._settings, "embed_jwt_secret", "")
    resp = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": _DEMO_COURSE_UID, "external_uid": _DEMO_TASK_EXTERNAL_UID_SC},
    )
    assert resp.status_code == 503
