"""tsk-559: фильтр активные/все/заблокированные — задания курса + люди.

Два независимых расширения query-параметров, оба read-only по смыслу:

* `GET /tasks/by-course/{id}?is_active=` — по образцу уже готового
  `GET /courses/{id}/materials?is_active=` (materials_extra.py). `None`
  (параметр не передан) — старое поведение, без фильтра.
* `GET /users/?blocked=` и `GET /users/search?blocked=` — блокировка это
  `blocked_at IS NOT NULL`, НЕ `is_active` (который означает «слит с другой
  учёткой», см. tsk-432/tsk-433). `blocked=true` -> `blocked_at IS NOT NULL`,
  `blocked=false` -> `IS NULL`, параметр не передан -> без фильтра (как было).
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _methodist_session(db) -> tuple[int, str]:
    u = Users(
        email=f"t559-methodist-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name="t559-methodist",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name = 'methodist' "
            "ON CONFLICT (user_id, role_id) DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _any_course_id(db) -> int:
    row = (await db.execute(text("SELECT id FROM courses LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет ни одного курса в БД")
    return int(row[0])


async def _create_task(db, *, course_id: int, is_active: bool) -> int:
    ext = f"t559-task-{random.randint(10**8, 10**10)}"
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
            "difficulty_id, is_active) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), :cid, 1, :active) RETURNING id"
        ),
        {
            "ext": ext,
            "content": json.dumps({"type": "SC", "stem": "t559-test"}),
            "cid": course_id,
            "active": is_active,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


@pytest.mark.asyncio
async def test_tasks_by_course_is_active_true_hides_inactive(db, client):
    course_id = await _any_course_id(db)
    _, token = await _methodist_session(db)
    active_id = await _create_task(db, course_id=course_id, is_active=True)
    inactive_id = await _create_task(db, course_id=course_id, is_active=False)

    r = await client.get(
        f"/api/v1/tasks/by-course/{course_id}?is_active=true&limit=1000",
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()}
    assert active_id in ids
    assert inactive_id not in ids


@pytest.mark.asyncio
async def test_tasks_by_course_is_active_false_shows_only_inactive(db, client):
    course_id = await _any_course_id(db)
    _, token = await _methodist_session(db)
    active_id = await _create_task(db, course_id=course_id, is_active=True)
    inactive_id = await _create_task(db, course_id=course_id, is_active=False)

    r = await client.get(
        f"/api/v1/tasks/by-course/{course_id}?is_active=false&limit=1000",
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()}
    assert inactive_id in ids
    assert active_id not in ids


@pytest.mark.asyncio
async def test_tasks_by_course_without_param_is_unchanged(db, client):
    """Регресс: параметр не передан -> оба видны, как до tsk-559."""
    course_id = await _any_course_id(db)
    _, token = await _methodist_session(db)
    active_id = await _create_task(db, course_id=course_id, is_active=True)
    inactive_id = await _create_task(db, course_id=course_id, is_active=False)

    r = await client.get(
        f"/api/v1/tasks/by-course/{course_id}?limit=1000", headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()}
    assert active_id in ids
    assert inactive_id in ids


@pytest.mark.asyncio
async def test_tasks_by_course_is_active_empty_result_for_no_match(db, client):
    course_id = await _any_course_id(db)
    _, token = await _methodist_session(db)
    # Курс без ни одного неактивного задания среди только что созданных —
    # запрашиваем заведомо несуществующий срез через фильтр по course_id,
    # созданный специально для этого теста (savepoint откатится сам).
    only_active_course_id = course_id
    active_id = await _create_task(db, course_id=only_active_course_id, is_active=True)

    r = await client.get(
        f"/api/v1/tasks/by-course/{only_active_course_id}"
        f"?is_active=false&difficulty_id=999999",
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert active_id  # использован только для настройки курса


async def _user(db, role: str | None, *, blocked: bool = False) -> tuple[int, str]:
    u = Users(
        email=f"t559-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t559-{role or 'norole'}-{random.randint(10**8, 10**10)}",
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
                "SELECT :u, id FROM roles WHERE name = :rn "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"u": u.id, "rn": role},
        )
    if blocked:
        await db.execute(
            text("UPDATE users SET blocked_at = now() WHERE id = :u"), {"u": u.id},
        )
    await db.commit()
    return u.id, token


@pytest.mark.asyncio
async def test_users_list_blocked_true_shows_only_blocked(db, client):
    _, admin_token = await _user(db, "admin")
    blocked_id, _ = await _user(db, "student", blocked=True)
    open_id, _ = await _user(db, "student", blocked=False)

    r = await client.get(
        "/api/v1/users/?role=student&blocked=true&limit=1000",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    ids = {u["id"] for u in r.json()["items"]}
    assert blocked_id in ids
    assert open_id not in ids


@pytest.mark.asyncio
async def test_users_list_blocked_false_shows_only_open(db, client):
    _, admin_token = await _user(db, "admin")
    blocked_id, _ = await _user(db, "student", blocked=True)
    open_id, _ = await _user(db, "student", blocked=False)

    r = await client.get(
        "/api/v1/users/?role=student&blocked=false&limit=1000",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    ids = {u["id"] for u in r.json()["items"]}
    assert open_id in ids
    assert blocked_id not in ids


@pytest.mark.asyncio
async def test_users_list_without_blocked_param_is_unchanged(db, client):
    """Регресс: параметр не передан -> и заблокированные, и открытые видны."""
    _, admin_token = await _user(db, "admin")
    blocked_id, _ = await _user(db, "student", blocked=True)
    open_id, _ = await _user(db, "student", blocked=False)

    r = await client.get(
        "/api/v1/users/?role=student&limit=1000", headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    ids = {u["id"] for u in r.json()["items"]}
    assert blocked_id in ids
    assert open_id in ids


@pytest.mark.asyncio
async def test_users_list_blocked_true_empty_result(db, client):
    """Ни одного заблокированного под редким именем-фильтром -> пустой список, не ошибка."""
    _, admin_token = await _user(db, "admin")
    unique_name = f"t559-nomatch-{random.randint(10**8, 10**10)}"

    r = await client.get(
        f"/api/v1/users/search?q={unique_name}&blocked=true",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


@pytest.mark.asyncio
async def test_users_search_blocked_filters_same_as_list(db, client):
    _, admin_token = await _user(db, "admin")
    shared_prefix = f"t559search{random.randint(10**8, 10**10)}"
    blocked_id, _ = await _user(db, None, blocked=True)
    open_id, _ = await _user(db, None, blocked=False)
    await db.execute(
        text("UPDATE users SET full_name = :n WHERE id = :u"),
        {"n": f"{shared_prefix}-blocked", "u": blocked_id},
    )
    await db.execute(
        text("UPDATE users SET full_name = :n WHERE id = :u"),
        {"n": f"{shared_prefix}-open", "u": open_id},
    )
    await db.commit()

    r = await client.get(
        f"/api/v1/users/search?q={shared_prefix}&blocked=true",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    ids = {u["id"] for u in r.json()}
    assert ids == {blocked_id}
