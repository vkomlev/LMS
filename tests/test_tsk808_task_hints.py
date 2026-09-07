"""tsk-808: подсказки задания правит методист, а условие остаётся под источником.

Ради чего отдельный эндпоинт. Соседний `PATCH /tasks/{id}` ставит
`content_provenance = manual_web` и тем выводит условие и правило проверки
из-под источника навсегда. Прикрепить видеоразбор — не то же самое, что
переписать условие: замораживать из-за подсказки весь текст задания нельзя.
Поэтому проверяем ровно две вещи, ради которых эндпоинт и заведён: подсказка
доезжает и считается в `has_hints`, а пометка ручной правки НЕ ставится.

Третья проверка — про мусор в ссылке: у ученика подсказка становится плеером
ВК (SPW, components/media/VideoEmbed.tsx), и строка вроде «спросить у Виктора»
превратилась бы у него в мёртвую внешнюю ссылку.
"""
from __future__ import annotations

import random
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()

TASKS_URL = "/api/v1/tasks/bulk-upsert"
EASY = 2
VIDEO = "https://vk.com/video-53400615_456239525"
VIDEO2 = "https://vk.com/video-53400615_456239412"


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t808-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t808-{role or 'norole'}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role},
        )
    await db.commit()
    return u.id, token


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'tsk-808', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": "test_tsk808_hints", "uid": f"lms:test:t808:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.flush()
    return int(row.id)


def _source_task(external_uid: str, course_id: int) -> dict[str, Any]:
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "difficulty_id": EASY,
        "task_content": {
            "type": "SC",
            "stem": "условие из источника",
            "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        },
        "solution_rules": {"type": "SC", "correct_options": ["a"], "max_score": 1},
        "max_score": 1,
    }


async def _post(client, items: list[dict[str, Any]]):
    return await client.post(TASKS_URL, params={"api_key": _api_key()}, json={"items": items})


async def _row(db, external_uid: str):
    row = (
        await db.execute(
            text(
                "SELECT id, task_content, content_provenance "
                "FROM tasks WHERE external_uid = :uid"
            ),
            {"uid": external_uid},
        )
    ).first()
    assert row is not None, f"задание {external_uid} не найдено"
    return row


async def _seed(db, client) -> tuple[int, str, str]:
    course_id = await _new_course(db)
    uid = f"t808-{uuid.uuid4().hex[:8]}"
    await _post(client, [_source_task(uid, course_id)])
    task = await _row(db, uid)
    _, token = await _user_with_session(db, "methodist")
    return int(task.id), uid, token


@pytest.mark.asyncio
async def test_hint_attached_and_counted(db, client):
    """Ссылка доезжает до задания, has_hints поднимается, снятие обнуляет."""
    task_id, uid, token = await _seed(db, client)

    r = await client.patch(
        f"/api/v1/tasks/{task_id}/hints",
        json={"hints_video": [VIDEO, VIDEO2, VIDEO]},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hints_video"] == [VIDEO, VIDEO2], "дубликат ссылки обязан схлопнуться"
    assert body["has_hints"] is True

    after = await _row(db, uid)
    assert after.task_content["hints_video"] == [VIDEO, VIDEO2]
    assert after.task_content["stem"] == "условие из источника", "условие трогать нельзя"

    off = await client.patch(
        f"/api/v1/tasks/{task_id}/hints", json={"hints_video": []}, headers=_auth(token)
    )
    assert off.status_code == 200, off.text
    assert off.json()["hints_video"] == []
    assert off.json()["has_hints"] is False, "подсказок не осталось — признак обязан упасть"


@pytest.mark.asyncio
async def test_hint_does_not_freeze_task_under_source(db, client):
    """Прикреплённая подсказка не помечает задание как правленое вручную.

    Иначе источник перестал бы обновлять условие у каждого задания, которому
    просто довезли разбор.
    """
    task_id, uid, token = await _seed(db, client)
    r = await client.patch(
        f"/api/v1/tasks/{task_id}/hints", json={"hints_video": [VIDEO]}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text

    after = await _row(db, uid)
    assert after.content_provenance is None, (
        "подсказка не должна выводить условие из-под источника — "
        "для этого есть отдельный PATCH /tasks/{id}"
    )
    assert r.json().get("content_provenance") is None


@pytest.mark.asyncio
async def test_hint_rejects_non_url(db, client):
    """Не-адрес отклоняется: у ученика подсказка превращается в плеер."""
    task_id, uid, token = await _seed(db, client)
    r = await client.patch(
        f"/api/v1/tasks/{task_id}/hints",
        json={"hints_video": ["спросить у Виктора"]},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text

    after = await _row(db, uid)
    assert not after.task_content.get("hints_video"), "при отказе ничего записываться не должно"


@pytest.mark.asyncio
async def test_hints_require_role(db, client):
    """Без роли методиста подсказки не правятся."""
    task_id, _uid, _token = await _seed(db, client)
    _, plain = await _user_with_session(db, None)
    r = await client.patch(
        f"/api/v1/tasks/{task_id}/hints", json={"hints_video": [VIDEO]}, headers=_auth(plain)
    )
    assert r.status_code == 403, r.text
