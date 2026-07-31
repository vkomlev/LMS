"""tsk-432: слияние профилей из кабинета — предпросмотр и применение.

Слияние необратимо и переносит данные по двум десяткам таблиц. Поэтому:

* **предпросмотр обязателен по смыслу** — он показывает, что именно переедет,
  ДО нажатия кнопки; здесь проверяем, что он считает реальные строки, а не
  выдаёт красивый ноль;
* **у каждого отказа своя причина.** `merge_users` возвращает голое `False` на
  все случаи сразу; человеку за экраном это ничего не объясняет, поэтому
  «сам с собой», «нет такого», «уже слит» разведены по разным сообщениям;
* **распоряжается только администратор** — методисту слияние закрыто.

Отдельно закреплена ловушка с порядком маршрутов: литеральный `/users/duplicates`
обязан регистрироваться РАНЬШЕ параметрического `/users/{user_id}`, иначе запрос
уходит в карточку человека и падает на попытке прочитать «duplicates» числом.
Тот же класс, что чинили в tsk-486.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user(db, role: str | None, *, name: str | None = None) -> tuple[int, str]:
    suffix = random.randint(10**8, 10**10)
    u = Users(
        email=f"t432m-{suffix}@example.com",
        password_hash=None,
        full_name=name or f"t432m-{role or 'norole'}-{suffix}",
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


@pytest.mark.asyncio
async def test_duplicates_route_not_swallowed_by_card(db, client):
    """`/users/duplicates` не должен уходить в карточку человека."""
    _, admin_token = await _user(db, "admin")
    r = await client.get("/api/v1/users/duplicates", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_preview_counts_real_rows(db, client):
    """Предпросмотр показывает то, что реально переедет."""
    source_id, _ = await _user(db, "student")
    target_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    r = await client.post(
        "/api/v1/users/merge/preview",
        json={"source_id": source_id, "target_id": target_id},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_id"] == source_id and body["target_id"] == target_id

    labels = {line["label"]: line["rows"] for line in body["lines"]}
    # у каждого заведённого тут человека есть привязка входа, роль и сеанс
    assert labels.get("способы входа") == 1
    assert labels.get("роли") == 1
    assert body["total_rows"] >= 2
    # подписи человеческие, а не имена таблиц
    assert all(line["label"] != f'{line["table"]}.{line["column"]}' for line in body["lines"])


@pytest.mark.asyncio
async def test_preview_changes_nothing(db, client):
    source_id, _ = await _user(db, "student")
    target_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    await client.post(
        "/api/v1/users/merge/preview",
        json={"source_id": source_id, "target_id": target_id},
        headers=_auth(admin_token),
    )
    still_active = (await db.execute(
        text("SELECT is_active FROM users WHERE id = :u"), {"u": source_id}
    )).scalar_one()
    assert still_active is True


@pytest.mark.asyncio
async def test_merge_moves_data_and_closes_source(db, client):
    source_id, _ = await _user(db, "student")
    target_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    r = await client.post(
        "/api/v1/users/merge",
        json={"source_id": source_id, "target_id": target_id},
        headers=_auth(admin_token),
    )
    assert r.status_code == 204, r.text

    row = (await db.execute(
        text("SELECT is_active, merged_into_user_id FROM users WHERE id = :u"),
        {"u": source_id},
    )).first()
    assert row.is_active is False
    assert row.merged_into_user_id == target_id

    # способы входа переехали — иначе слитая учётка осталась бы рабочей
    left = (await db.execute(
        text("SELECT count(*) FROM identity_link WHERE user_id = :u"), {"u": source_id}
    )).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_each_refusal_has_its_own_reason(db, client):
    source_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    same = await client.post(
        "/api/v1/users/merge/preview",
        json={"source_id": source_id, "target_id": source_id},
        headers=_auth(admin_token),
    )
    assert same.status_code == 422, same.text
    assert "саму с собой" in same.json()["detail"]

    missing = await client.post(
        "/api/v1/users/merge/preview",
        json={"source_id": source_id, "target_id": 999_999_999},
        headers=_auth(admin_token),
    )
    assert missing.status_code == 404, missing.text
    assert "не найдена" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_merge_already_merged(db, client):
    source_id, _ = await _user(db, "student")
    target_id, _ = await _user(db, "student")
    third_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    await client.post(
        "/api/v1/users/merge",
        json={"source_id": source_id, "target_id": target_id},
        headers=_auth(admin_token),
    )
    again = await client.post(
        "/api/v1/users/merge",
        json={"source_id": source_id, "target_id": third_id},
        headers=_auth(admin_token),
    )
    assert again.status_code == 409, again.text
    assert "уже слита" in again.json()["detail"]


@pytest.mark.asyncio
async def test_merge_is_closed_to_methodist(db, client):
    source_id, _ = await _user(db, "student")
    target_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")

    for path in ("/api/v1/users/merge/preview", "/api/v1/users/merge"):
        r = await client.post(
            path,
            json={"source_id": source_id, "target_id": target_id},
            headers=_auth(methodist_token),
        )
        assert r.status_code == 403, f"{path}: {r.text}"

    listed = await client.get("/api/v1/users/duplicates", headers=_auth(methodist_token))
    assert listed.status_code == 403, listed.text
