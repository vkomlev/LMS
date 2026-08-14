"""tsk-423: лимит гостевых заданий на demo-курс (`courses.demo_task_limit`).

Курс с NULL `demo_task_limit` (default, напр. пилотное «Пробное занятие»,
course_id=651 на проде) — прежнее поведение без лимита, покрыто существующими
тестами test_y5_guest_endpoints.py (демо-курс без лимита там продолжает
работать без изменений).

Этот файл — курс с `demo_task_limit=1` и ДВУМЯ заданиями: гость может
проверить ровно одно ИЗ РАЗНЫХ, повторные попытки на уже использованном не
считаются, а второе (новое) задание блокируется с payload.code=demo_limit_reached.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text


# tsk-611: счётчик гостевых заданий и rate-limit хранятся в Redis (см. conftest).
pytestmark = pytest.mark.requires_redis

_DEMO_COURSE_UID = "pytest:tsk423-limited-demo"
_TASK_A_EXT = "pytest:tsk423:task-a"
_TASK_B_EXT = "pytest:tsk423:task-b"
_CORRECT_OPTION_ID = "A"


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_y5_rate_limit_keys():
    """Тот же сброс rate-limit, что в test_y5_guest_endpoints.py — иначе 429."""
    import os
    import redis.asyncio as aioredis

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis = aioredis.from_url(redis_url, decode_responses=True)
    patterns = ["guest_session:*", "guest_read:*", "guest_attempt:*", "guest_attempt_session:*"]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture
async def limited_demo_course(db):
    """Курс `is_public_demo=true, demo_task_limit=1` с 2 заданиями (SC)."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo, demo_task_limit) "
                "VALUES ('pytest tsk423 limited demo', 'self_guided', :uid, TRUE, 1) "
                "RETURNING id"
            ),
            {"uid": _DEMO_COURSE_UID},
        )
    ).scalar_one()

    task_ids = {}
    for label, ext_uid in (("a", _TASK_A_EXT), ("b", _TASK_B_EXT)):
        task_content = {
            "type": "SC",
            "stem": f"Задание {label}",
            "options": [
                {"id": _CORRECT_OPTION_ID, "text": "Верно"},
                {"id": "B", "text": "Неверно"},
            ],
        }
        solution_rules = {"max_score": 1, "correct_options": [_CORRECT_OPTION_ID]}
        task_ids[label] = int(
            (
                await db.execute(
                    text(
                        "INSERT INTO tasks "
                        "(external_uid, max_score, task_content, course_id, difficulty_id, solution_rules) "
                        "VALUES (:uid, 1, CAST(:tc AS jsonb), :cid, :did, CAST(:sr AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "uid": ext_uid,
                        "tc": json.dumps(task_content, ensure_ascii=False),
                        "cid": course_id,
                        "did": difficulty_id,
                        "sr": json.dumps(solution_rules),
                    },
                )
            ).scalar_one()
        )
    await db.commit()
    try:
        yield {"course_id": course_id, "task_a": task_ids["a"], "task_b": task_ids["b"]}
    finally:
        await db.execute(text("DELETE FROM courses WHERE course_uid = :uid"), {"uid": _DEMO_COURSE_UID})
        await db.commit()


def _submit_payload(task_id: int, option_id: str = _CORRECT_OPTION_ID) -> dict:
    return {"task_id": task_id, "answer": {"type": "SC", "response": {"selected_option_ids": [option_id]}}}


@pytest.mark.asyncio
async def test_first_task_within_limit_allowed(client, limited_demo_course):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    resp = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"])
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_repeat_attempt_on_same_task_not_counted_twice(client, limited_demo_course):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    first = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"], "B")
    )
    assert first.status_code == 201
    assert first.json()["is_correct"] is False

    # Повторная попытка на ТОМ ЖЕ задании — не новый расход лимита.
    second = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"])
    )
    assert second.status_code == 201, second.text
    assert second.json()["is_correct"] is True


@pytest.mark.asyncio
async def test_second_distinct_task_blocked_after_limit(client, limited_demo_course):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    first = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"])
    )
    assert first.status_code == 201

    blocked = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_b"])
    )
    assert blocked.status_code == 403, blocked.text
    body = blocked.json()
    assert body["payload"]["code"] == "demo_limit_reached"
    assert body["payload"]["limit"] == 1
    assert body["payload"]["used"] == 1


@pytest.mark.asyncio
async def test_get_task_blocked_after_limit_reached(client, limited_demo_course):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    first = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"])
    )
    assert first.status_code == 201

    # GET нового (второго) задания после исчерпания лимита — тоже 403, не 200.
    resp = await client.get(f"/api/v1/learning/guest/task/{limited_demo_course['task_b']}")
    assert resp.status_code == 403, resp.text
    assert resp.json()["payload"]["code"] == "demo_limit_reached"


@pytest.mark.asyncio
async def test_get_already_used_task_still_allowed_after_limit(client, limited_demo_course):
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201

    first = await client.post(
        "/api/v1/learning/guest/attempts", json=_submit_payload(limited_demo_course["task_a"])
    )
    assert first.status_code == 201

    # Уже использованное задание остаётся доступным для повторного просмотра/попытки.
    resp = await client.get(f"/api/v1/learning/guest/task/{limited_demo_course['task_a']}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_cookie_get_treated_as_zero_used(client, limited_demo_course):
    """Без guest_session cookie — историю проверять не по чему, не блокируем."""
    resp = await client.get(f"/api/v1/learning/guest/task/{limited_demo_course['task_a']}")
    assert resp.status_code == 200
