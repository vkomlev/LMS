"""tsk-553: кабинет методиста — GET/PATCH /courses/{id}/sampling.

Продолжение tsk-314 (движок выборки заданий по сложности на подкурс уже
готов и задеплоен, БД-поле `courses.sampling_config` тоже). До этой задачи
включить/настроить выборку можно было только прямым SQL — здесь методист
получает эндпоинт кабинета. Гейт — тот же `_STRUCTURE_GATE`
(methodist/admin), что у `PATCH /courses/{id}/card` и `/structure` (tsk-433):
методической настройкой курса управляет методист, не преподаватель и не
ученик.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t553-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t553-{role or 'norole'}",
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


async def _course(db, title: str = "t553") -> int:
    c = Courses(
        title=f"{title}-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
        course_uid=f"lms:test:t553:{uuid.uuid4().hex[:12]}",
    )
    db.add(c)
    await db.flush()
    await db.commit()
    return c.id


async def _difficulty_id(db, code: str) -> int:
    row = (await db.execute(text("SELECT id FROM difficulties WHERE code = :c"), {"c": code})).first()
    if row is None:
        pytest.skip(f"нет difficulty с кодом {code}")
    return int(row[0])


async def _new_task(db, *, course_id: int, difficulty_id: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
            "VALUES (CAST(:tc AS jsonb), :cid, :did, :uid) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk553"}',
            "cid": course_id,
            "did": difficulty_id,
            "uid": f"tsk553-{uuid.uuid4().hex[:12]}",
        },
    )
    return int(res.scalar_one())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_sampling_default_null_with_task_count(db, client):
    """Новый курс: sampling_config=null, easy_normal_count считает реальные задания."""
    course = await _course(db, "get-default")
    easy_id = await _difficulty_id(db, "EASY")
    normal_id = await _difficulty_id(db, "NORMAL")
    await _new_task(db, course_id=course, difficulty_id=easy_id)
    await _new_task(db, course_id=course, difficulty_id=easy_id)
    await _new_task(db, course_id=course, difficulty_id=normal_id)
    await db.commit()
    _, token = await _user_with_session(db, "methodist")

    r = await client.get(f"/api/v1/courses/{course}/sampling", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sampling_config"] is None
    assert body["easy_normal_count"] == 3


@pytest.mark.asyncio
async def test_patch_sets_config_and_get_reflects_it(db, client):
    """Методист включает выборку — PATCH пишет, GET сразу видит новое значение."""
    course = await _course(db, "patch-set")
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": {"enabled": True, "threshold": 30, "easy_ratio": 0.5}},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["sampling_config"] == {"enabled": True, "threshold": 30, "easy_ratio": 0.5}

    r = await client.get(f"/api/v1/courses/{course}/sampling", headers=_auth(token))
    assert r.json()["sampling_config"] == {"enabled": True, "threshold": 30, "easy_ratio": 0.5}


@pytest.mark.asyncio
async def test_patch_null_resets_config(db, client):
    """sampling_config: null сбрасывает выборку — прежнее поведение движка."""
    course = await _course(db, "patch-reset")
    _, token = await _user_with_session(db, "methodist")

    await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": {"enabled": True, "threshold": 20, "easy_ratio": 0.3}},
        headers=_auth(token),
    )
    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": None},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["sampling_config"] is None


@pytest.mark.asyncio
async def test_patch_rejects_invalid_threshold(db, client):
    """threshold < 1 -> 422: движок трактует threshold=0 как «выборки нет» молча,
    эндпоинт обязан отказать явно, а не тихо принять бессмысленное значение."""
    course = await _course(db, "patch-invalid")
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": {"enabled": True, "threshold": 0, "easy_ratio": 0.5}},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_rejects_invalid_ratio(db, client):
    """easy_ratio вне [0,1] -> 422."""
    course = await _course(db, "patch-invalid-ratio")
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": {"enabled": True, "threshold": 30, "easy_ratio": 1.5}},
        headers=_auth(token),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_get_404_for_missing_course(db, client):
    _, token = await _user_with_session(db, "methodist")
    r = await client.get("/api/v1/courses/999999999/sampling", headers=_auth(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_student_denied(db, client):
    course = await _course(db, "denied-student")
    _, token = await _user_with_session(db, "student")

    r = await client.get(f"/api/v1/courses/{course}/sampling", headers=_auth(token))
    assert r.status_code == 403

    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        json={"sampling_config": {"enabled": True, "threshold": 30, "easy_ratio": 0.5}},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_teacher_denied(db, client):
    """Преподаватель видит дерево курсов, но методические настройки — не его зона (как /card, /structure)."""
    course = await _course(db, "denied-teacher")
    _, token = await _user_with_session(db, "teacher")

    r = await client.get(f"/api/v1/courses/{course}/sampling", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_key_still_works(db, client):
    """ТГ-боты ходят с `?api_key=` — тот же паттерн совместимости, что в tsk-433."""
    course = await _course(db, "svc-key")
    key = {"api_key": _api_key()}

    r = await client.get(f"/api/v1/courses/{course}/sampling", params=key)
    assert r.status_code == 200, r.text

    r = await client.patch(
        f"/api/v1/courses/{course}/sampling",
        params=key,
        json={"sampling_config": {"enabled": True, "threshold": 30, "easy_ratio": 0.5}},
    )
    assert r.status_code == 200, r.text
