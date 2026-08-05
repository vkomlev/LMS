"""tsk-567 — `/tasks/search` и `/materials/search` не находили запись по
числовому ID: искали только текст (stem/title/external_uid ILIKE), а
external_uid не связан с видимым ID (напр. `tg:ege:960` для `tasks.id=3021`).
Операторская находка живьём сразу после деплоя tsk-564/tsk-565.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.models.courses import Courses
from app.models.materials import Materials
from app.models.tasks import Tasks
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk567"


async def _methodist_session(db) -> str:
    u = Users(
        email=f"{_TAG}-methodist-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-methodist",
        tg_id=None,
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
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def id_search_graph(db):
    """Задание и материал, чей external_uid/title НЕ содержит их видимый ID
    (как реальный tasks.id=3021/external_uid='tg:ege:960') — текстовый ILIKE
    в принципе не может их найти по номеру."""
    ids: dict[str, int] = {}
    try:
        course = Courses(
            title=f"{_TAG}-course-{random.randint(10**8, 10**10)}",
            access_level="self_guided",
            course_uid=f"lms:test:{_TAG}:{uuid.uuid4().hex[:12]}",
        )
        db.add(course)
        await db.flush()
        ids["course"] = course.id

        difficulty_id = (
            await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
        ).scalar()

        task = Tasks(
            task_content={"type": "SC", "stem": f"{_TAG} совсем не содержит свой номер", "title": ""},
            solution_rules={"max_score": 10, "correct_options": ["A"]},
            course_id=course.id,
            difficulty_id=difficulty_id,
            external_uid=f"tg:ege:{_TAG}",
            max_score=10,
        )
        material = Materials(
            course_id=course.id,
            title=f"{_TAG} тоже без номера в заголовке",
            type="text",
            content={"text": "x"},
            external_uid=f"wp_nav:{_TAG}",
        )
        db.add_all([task, material])
        await db.flush()
        ids["task"] = task.id
        ids["material"] = material.id
        await db.commit()

        yield ids
    finally:
        await db.rollback()
        if ids.get("task"):
            await db.execute(text("DELETE FROM task_results WHERE task_id = :t"), {"t": ids["task"]})
            await db.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": ids["task"]})
        if ids.get("material"):
            await db.execute(text("DELETE FROM materials WHERE id = :m"), {"m": ids["material"]})
        if ids.get("course"):
            await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": ids["course"]})
        await db.commit()


async def test_task_search_by_plain_numeric_id(db, id_search_graph, client):
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": str(id_search_graph["task"])},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [t["id"] for t in resp.json()]
    assert found == [id_search_graph["task"]]


async def test_task_search_by_id_prefixed_query(db, id_search_graph, client):
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": f"id-{id_search_graph['task']}"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [t["id"] for t in resp.json()]
    assert found == [id_search_graph["task"]]


async def test_task_search_by_id_not_in_course_filter_returns_empty(db, id_search_graph, client):
    """ID существует, но не в запрошенном course_id — курс-фильтр применяется и к ID-режиму."""
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": str(id_search_graph["task"]), "course_id": id_search_graph["course"] + 999999},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_material_search_by_plain_numeric_id(db, id_search_graph, client):
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/materials/search",
        params={"q": str(id_search_graph["material"])},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [m["id"] for m in resp.json()["items"]]
    assert found == [id_search_graph["material"]]


async def test_material_search_by_id_prefixed_query(db, id_search_graph, client):
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/materials/search",
        params={"q": f"ID-{id_search_graph['material']}"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [m["id"] for m in resp.json()["items"]]
    assert found == [id_search_graph["material"]]


async def test_task_search_text_mode_unaffected_by_id_parsing(db, id_search_graph, client):
    """Обычный текстовый запрос (не число, не id-N) по-прежнему работает — нет регресса."""
    token = await _methodist_session(db)
    resp = await client.get(
        "/api/v1/tasks/search",
        params={"q": f"{_TAG} совсем не содержит"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    found = [t["id"] for t in resp.json()]
    assert id_search_graph["task"] in found
