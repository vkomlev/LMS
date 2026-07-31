"""tsk-432: назначение и снятие ролей из кабинета администратора.

Три вещи, ради которых тест написан:

* **Запись была недоступна из браузера.** Оба пути висели на legacy `?api_key=`
  — тот же разрыв, что закрывали у контента в Волнах 2-3.
* **Роли выдают права, а не учебные обязанности.** Ролью выдаётся в том числе
  доступ администратора, поэтому запись уже чтения: методист роли видит, но не
  меняет.
* **Школу нельзя запереть.** Снять роль администратора у последнего
  администратора со входом нельзя: вернуть её будет некому. Себя обезоруживать
  тоже не даём — та же защита, что у блокировки.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t432r-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t432r-{role or 'norole'}",
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _role_id(db, name: str) -> int:
    return (await db.execute(
        text("SELECT id FROM roles WHERE name = :n"), {"n": name}
    )).scalar_one()


async def _role_names(db, user_id: int) -> set[str]:
    rows = await db.execute(
        text(
            "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :u"
        ),
        {"u": user_id},
    )
    return set(rows.scalars().all())


@pytest.mark.asyncio
async def test_roles_catalog_reachable_from_browser(db, client):
    """Справочник ролей нужен экрану, чтобы было из чего выбирать."""
    _, admin_token = await _user(db, "admin")
    r = await client.get("/api/v1/roles/catalog", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    names = {item["name"] for item in r.json()}
    assert {"admin", "methodist", "teacher", "student"} <= names


@pytest.mark.asyncio
async def test_catalog_not_swallowed_by_generic_crud(db, client):
    """`/roles/catalog` не должен уйти в `/roles/{item_id}`."""
    _, admin_token = await _user(db, "admin")
    r = await client.get("/api/v1/roles/catalog", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_admin_assigns_and_removes_role(db, client):
    person_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")
    teacher_role = await _role_id(db, "teacher")

    added = await client.post(
        f"/api/v1/users/{person_id}/roles/{teacher_role}", headers=_auth(admin_token)
    )
    assert added.status_code == 204, added.text
    assert "teacher" in await _role_names(db, person_id)

    removed = await client.delete(
        f"/api/v1/users/{person_id}/roles/{teacher_role}", headers=_auth(admin_token)
    )
    assert removed.status_code == 204, removed.text
    assert "teacher" not in await _role_names(db, person_id)


@pytest.mark.asyncio
async def test_methodist_can_read_roles_but_not_change(db, client):
    """Методист роли видит — он ведёт учебный процесс, но правами не распоряжается."""
    person_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")
    teacher_role = await _role_id(db, "teacher")

    listed = await client.get(
        f"/api/v1/users/{person_id}/roles/", headers=_auth(methodist_token)
    )
    assert listed.status_code == 200, listed.text

    added = await client.post(
        f"/api/v1/users/{person_id}/roles/{teacher_role}", headers=_auth(methodist_token)
    )
    assert added.status_code == 403, added.text
    assert "teacher" not in await _role_names(db, person_id)


@pytest.mark.asyncio
async def test_cannot_disarm_self(db, client):
    admin_id, admin_token = await _user(db, "admin")
    await _user(db, "admin")  # второй админ, чтобы сработала именно защита «сам себя»
    admin_role = await _role_id(db, "admin")

    r = await client.delete(
        f"/api/v1/users/{admin_id}/roles/{admin_role}", headers=_auth(admin_token)
    )
    assert r.status_code == 409, r.text
    assert "самого себя" in r.json()["detail"]
    assert "admin" in await _role_names(db, admin_id)


@pytest.mark.asyncio
async def test_cannot_remove_last_admin_role(db, client):
    """Иначе школа осталась бы без человека, способного вернуть права."""
    victim_id, _ = await _user(db, "admin")
    await db.execute(
        text(
            "UPDATE users SET blocked_at = now() WHERE id <> :keep AND id IN ("
            " SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
            " WHERE r.name = 'admin')"
        ),
        {"keep": victim_id},
    )
    await db.commit()
    admin_role = await _role_id(db, "admin")

    from app.core.config import Settings
    key = next(iter(Settings().valid_api_keys))
    r = await client.delete(
        f"/api/v1/users/{victim_id}/roles/{admin_role}?api_key={key}"
    )
    assert r.status_code == 409, r.text
    assert "последний администратор" in r.json()["detail"].lower()
    assert "admin" in await _role_names(db, victim_id)


@pytest.mark.asyncio
async def test_other_roles_removed_freely(db, client):
    """Защита только про роль администратора — остальные снимаются как обычно."""
    person_id, _ = await _user(db, "teacher")
    _, admin_token = await _user(db, "admin")
    teacher_role = await _role_id(db, "teacher")

    r = await client.delete(
        f"/api/v1/users/{person_id}/roles/{teacher_role}", headers=_auth(admin_token)
    )
    assert r.status_code == 204, r.text
    assert "teacher" not in await _role_names(db, person_id)
