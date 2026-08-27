"""Регрессия tsk-702: гостевой и embed-контур проверяют `tasks.is_active`.

Дыра — последний хвост линии tsk-695 (материал) → tsk-697 (одна ручка задания) →
tsk-699 (список заданий курса) → tsk-701 (приём ответа). Там закрыли УЧЕНИКА, на
чтении и на записи. Гость ходит своей веткой и ученических гейтов не касается
вовсе: все четыре выборки контура фильтровали только по `courses.is_public_demo`,
но не по активности задания. Снятое с публикации задание открывалось анонимно,
без всякой авторизации, и принимало ответ. На проде 26.08.2026 — 63 выключенных
задания из 4146 в публичных демо-курсах.

Четыре двери, и все четыре свои — общей выборки у контура нет:

1. `GET /api/v1/learning/guest/task/{task_id}` → `learning_guest_service.get_demo_task`
2. `POST /api/v1/learning/guest/attempts` → `learning_guest_service.submit_guest_attempt`
3. `POST /api/v1/embed-api/auth/issue` — своя выборка в `embed_api`
4. `GET /api/v1/embed-api/courses/{uid}/task/{external_uid}` — тоже своя

Фикс: общий предикат `is_task_visible_to_guest` (журналирует отказ), вызывается
во всех четырёх точках. Четвёртая проверяется отдельно от третьей намеренно:
токен живёт 5 минут, и задание могут выключить между выдачей и чтением.

Рядом с каждым отказом стоит контроль на АКТИВНОМ задании: без него тест не
отличит закрытую дыру от погашенной витрины.

Тесты бьют по HTTP (дыра была в HTTP-слое). Требуют живого Redis: на нём стоят
гостевые сессии и лимиты частоты Y-5.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.requires_redis

_DEMO_COURSE_UID = "pytest:tsk702-public-demo"
_UID_ACTIVE = "pytest:tsk702:active"
_UID_INACTIVE = "pytest:tsk702:inactive"
_CORRECT_OPTION_ID = "A"
_TEST_EMBED_SECRET = "tsk702-test-secret"


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_rate_limit_keys():
    """Очистить ключи частоты Y-5 — иначе повторный прогон встречает 429."""
    import os

    import redis.asyncio as aioredis

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/2"), decode_responses=True
    )
    patterns = [
        "guest_session:*",
        "guest_read:*",
        "guest_attempt:*",
        "guest_attempt_session:*",
        "embed_issue:*",
        "embed_read:*",
        "embed_jti:*",
    ]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(scope="function")
async def demo(db):
    """Публичный демо-курс с двумя SC-заданиями: активным и выключенным."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    course_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                    "VALUES ('pytest tsk702 demo', 'self_guided', :uid, TRUE) RETURNING id"
                ),
                {"uid": _DEMO_COURSE_UID},
            )
        ).scalar_one()
    )

    task_content = {
        "type": "SC",
        "stem": "Выберите правильный вариант.",
        "options": [
            {"id": _CORRECT_OPTION_ID, "text": "Правильный вариант"},
            {"id": "B", "text": "Неправильный вариант"},
        ],
    }
    solution_rules = {"max_score": 1, "correct_options": [_CORRECT_OPTION_ID]}

    async def _make(external_uid: str, *, is_active: bool) -> int:
        return int(
            (
                await db.execute(
                    text(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, is_active) VALUES "
                        "(:uid, 1, CAST(:tc AS jsonb), :cid, :did, CAST(:sr AS jsonb), :act) "
                        "RETURNING id"
                    ),
                    {
                        "uid": external_uid,
                        "tc": json.dumps(task_content, ensure_ascii=False),
                        "cid": course_id,
                        "did": difficulty_id,
                        "sr": json.dumps(solution_rules),
                        "act": is_active,
                    },
                )
            ).scalar_one()
        )

    ids = {
        "course": course_id,
        "active": await _make(_UID_ACTIVE, is_active=True),
        "inactive": await _make(_UID_INACTIVE, is_active=False),
    }
    await db.commit()
    try:
        yield ids
    finally:
        await db.execute(
            text("DELETE FROM guest_attempt WHERE task_id = ANY(:t)"),
            {"t": [ids["active"], ids["inactive"]]},
        )
        await db.execute(
            text("DELETE FROM courses WHERE course_uid = :uid"), {"uid": _DEMO_COURSE_UID}
        )
        await db.commit()


@pytest.fixture()
def embed_secret_set(monkeypatch):
    """Подменить секрет embed-токена в модуле embed-api."""
    from app.api.v1 import embed_api as embed_module

    monkeypatch.setattr(embed_module._settings, "embed_jwt_secret", _TEST_EMBED_SECRET)
    monkeypatch.setattr(embed_module._settings, "embed_jwt_ttl_sec", 300)


async def _guest_attempts_count(db, task_id: int) -> int:
    return int(
        (
            await db.execute(
                text("SELECT COUNT(*) FROM guest_attempt WHERE task_id = :t"), {"t": task_id}
            )
        ).scalar_one()
    )


# ─── дверь 1: чтение задания гостем ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_read_404_on_inactive_task(client, demo):
    """Выключенное задание гостю не открывается; активное — открывается."""
    ok = await client.get(f"/api/v1/learning/guest/task/{demo['active']}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["stem"]

    denied = await client.get(f"/api/v1/learning/guest/task/{demo['inactive']}")
    assert denied.status_code == 404, denied.text
    assert "stem" not in denied.text


# ─── дверь 2: приём ответа гостя ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guest_attempt_404_on_inactive_task(client, db, demo):
    """Выключенное задание не принимает ответ гостя и не пишет guest_attempt."""
    sess = await client.post("/api/v1/learning/guest/session")
    assert sess.status_code == 201, sess.text

    before = await _guest_attempts_count(db, demo["inactive"])
    denied = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": demo["inactive"],
            "answer": {"type": "SC", "response": {"selected_option_ids": [_CORRECT_OPTION_ID]}},
        },
    )
    assert denied.status_code == 404, denied.text
    assert await _guest_attempts_count(db, demo["inactive"]) == before

    accepted = await client.post(
        "/api/v1/learning/guest/attempts",
        json={
            "task_id": demo["active"],
            "answer": {"type": "SC", "response": {"selected_option_ids": [_CORRECT_OPTION_ID]}},
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["is_correct"] is True


# ─── дверь 3: выдача embed-токена ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_issue_404_on_inactive_task(client, demo, embed_secret_set):
    """Токен на iframe выключенного задания не выдаётся; на активное — выдаётся."""
    ok = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": _DEMO_COURSE_UID, "external_uid": _UID_ACTIVE},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["token"]

    denied = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": _DEMO_COURSE_UID, "external_uid": _UID_INACTIVE},
    )
    assert denied.status_code == 404, denied.text


# ─── дверь 4: чтение задания по выданному токену ────────────────────────────

@pytest.mark.asyncio
async def test_embed_read_404_when_task_disabled_after_issue(client, db, demo, embed_secret_set):
    """Задание выключили после выдачи токена — чтение по нему уже отказывает.

    Ровно тот зазор, ради которого дверь 4 проверяется отдельно от двери 3:
    токен живёт 5 минут, и всё это время старая витрина держала бы блок живым.
    """
    issued = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": _DEMO_COURSE_UID, "external_uid": _UID_ACTIVE},
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]

    await db.execute(
        text("UPDATE tasks SET is_active = false WHERE id = :t"), {"t": demo["active"]}
    )
    await db.commit()

    denied = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_UID_ACTIVE}",
        params={"token": token},
    )
    assert denied.status_code == 404, denied.text
    assert "stem" not in denied.text


@pytest.mark.asyncio
async def test_embed_read_200_on_active_task(client, demo, embed_secret_set):
    """Контроль: активное задание по свежему токену читается как раньше."""
    issued = await client.post(
        "/embed-api/auth/issue",
        json={"course_uid": _DEMO_COURSE_UID, "external_uid": _UID_ACTIVE},
    )
    assert issued.status_code == 200, issued.text

    ok = await client.get(
        f"/embed-api/courses/{_DEMO_COURSE_UID}/task/{_UID_ACTIVE}",
        params={"token": issued.json()["token"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["stem"]


# ─── предикат напрямую: журнал отказа ───────────────────────────────────────

@pytest.mark.asyncio
async def test_denial_is_logged(caplog):
    """Отказ пишется в журнал: иначе не узнать, какая страница WP погасла."""
    import logging
    from types import SimpleNamespace

    from app.services.learning_guest_service import is_task_visible_to_guest

    with caplog.at_level(logging.INFO, logger="app.services.learning_guest_service"):
        assert is_task_visible_to_guest(
            SimpleNamespace(id=1, course_id=2, is_active=True), surface="read"
        ) is True
        assert not caplog.records

        assert is_task_visible_to_guest(
            SimpleNamespace(id=7, course_id=9, is_active=False), surface="embed_read"
        ) is False

    assert any("tsk-702" in r.getMessage() and "task_id=7" in r.getMessage()
               for r in caplog.records), caplog.text
