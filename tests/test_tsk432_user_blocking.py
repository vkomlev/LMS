"""tsk-432: блокировка учётной записи закрывает вход по-настоящему.

Чего здесь добиваемся и почему именно так:

* **Блокировка ≠ слияние.** `is_active=false` уже означает «учётка слита в
  другую», и такие записи списки скрывают. Заблокированный человек должен
  остаться видимым: его работы и история нужны преподавателю. Поэтому отдельный
  признак `blocked_at`, а не переиспользование `is_active`.
* **Действует сразу.** Проверка стоит в `get_current_user`, который грузит
  пользователя на каждом запросе. Если бы её там не было, человек продолжал бы
  работать в открытой вкладке до протухания токена — то есть блокировка была бы
  отложенной, а выглядела бы мгновенной.
* **403, а не 401.** Ключ доступа исправен, закрыт сам аккаунт. На 401 портал
  увёл бы человека на форму входа, он вошёл бы снова и снова получил отказ —
  петля вместо объяснения.
* **Нельзя запереть школу.** Себя и последнего администратора со входом
  заблокировать нельзя: разблокировать станет некому, кроме правки в базе.
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
        email=f"t432-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t432-{role or 'norole'}",
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


async def _blocked_at(db, user_id: int):
    return (await db.execute(
        text("SELECT blocked_at FROM users WHERE id = :u"), {"u": user_id}
    )).scalar_one()


@pytest.mark.asyncio
async def test_blocked_user_loses_access_immediately(db, client):
    """Открытая сессия перестаёт работать сразу, а не после протухания."""
    victim_id, victim_token = await _user(db, "student")
    _, admin_token = await _user(db, "admin")

    before = await client.get("/api/v1/me", headers=_auth(victim_token))
    assert before.status_code == 200, before.text

    blocked = await client.post(
        f"/api/v1/users/{victim_id}/block",
        json={"reason": "тестовая причина"},
        headers=_auth(admin_token),
    )
    assert blocked.status_code == 200, blocked.text

    # Сеансы оборваны, поэтому старый токен даёт именно «не авторизован» —
    # это правда: сессии больше нет.
    after = await client.get("/api/v1/me", headers=_auth(victim_token))
    assert after.status_code == 401, after.text

    # А вот свежая сессия заблокированного (если бы её кто-то выдал в обход
    # путей входа) упирается во второй рубеж — проверку в самом доступе.
    fresh, _, _ = await create_session(db, user_id=victim_id)
    await db.commit()
    denied = await client.get("/api/v1/me", headers=_auth(fresh))
    assert denied.status_code == 403, denied.text
    assert "закрыт" in denied.json()["detail"].lower()


@pytest.mark.asyncio
async def test_block_does_not_hide_person_from_lists(db, client):
    """Заблокированный остаётся видимым: его работы нужны преподавателю."""
    victim_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")
    await client.post(f"/api/v1/users/{victim_id}/block", headers=_auth(admin_token))

    card = await client.get(f"/api/v1/users/{victim_id}", headers=_auth(admin_token))
    assert card.status_code == 200, card.text
    # is_active трогать нельзя — он означает «слит», а человек не слит
    assert await _blocked_at(db, victim_id) is not None
    is_active = (await db.execute(
        text("SELECT is_active FROM users WHERE id = :u"), {"u": victim_id}
    )).scalar_one()
    assert is_active is True


@pytest.mark.asyncio
async def test_unblock_restores_access(db, client):
    victim_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")
    await client.post(f"/api/v1/users/{victim_id}/block", headers=_auth(admin_token))

    r = await client.post(f"/api/v1/users/{victim_id}/unblock", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    assert await _blocked_at(db, victim_id) is None

    # сеансы не воскресают — нужен новый вход
    fresh, _, _ = await create_session(db, user_id=victim_id)
    await db.commit()
    ok = await client.get("/api/v1/me", headers=_auth(fresh))
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_cannot_block_self(db, client):
    admin_id, admin_token = await _user(db, "admin")
    await _user(db, "admin")  # второй админ, чтобы сработала именно защита «сам себя»

    r = await client.post(f"/api/v1/users/{admin_id}/block", headers=_auth(admin_token))
    assert r.status_code == 409, r.text
    assert await _blocked_at(db, admin_id) is None


@pytest.mark.asyncio
async def test_cannot_block_last_admin(db, client):
    """Иначе школа осталась бы без администратора, и снять блок было бы нечем."""
    victim_id, _ = await _user(db, "admin")
    # Все остальные администраторы уже без входа — victim остался единственным.
    await db.execute(
        text(
            "UPDATE users SET blocked_at = now() WHERE id <> :keep AND id IN ("
            " SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id"
            " WHERE r.name = 'admin')"
        ),
        {"keep": victim_id},
    )
    await db.commit()

    # Блокируем сервисным ключом: у него нет своего id, и защита «сам себя» не
    # сработает — проверяем именно защиту последнего администратора.
    from app.core.config import Settings
    key = next(iter(Settings().valid_api_keys))
    r = await client.post(f"/api/v1/users/{victim_id}/block?api_key={key}")
    assert r.status_code == 409, r.text
    assert "администратор" in r.json()["detail"].lower()
    assert await _blocked_at(db, victim_id) is None


@pytest.mark.asyncio
async def test_blocked_cannot_log_in_again(db, client):
    """Отказ приходит на входе, а не после «успешного» входа."""
    victim_id, _ = await _user(db, "student")
    _, admin_token = await _user(db, "admin")
    await client.post(f"/api/v1/users/{victim_id}/block", headers=_auth(admin_token))

    # Запрос ссылки (`/auth/magic-link/send`) здесь не дёргаем: он реально шлёт
    # письмо через внешний сервис. Да и отказывать на этом шаге не следует —
    # это выдало бы, существует ли аккаунт с таким адресом. Отказ живёт на шаге
    # ВХОДА по ссылке, и проверяем именно его — на всех трёх путях входа стоит
    # один и тот же `assert_not_blocked`.
    from app.services import user_block_service
    from app.utils.exceptions import DomainError

    with pytest.raises(DomainError) as err:
        await user_block_service.assert_not_blocked(db, victim_id)
    assert err.value.status_code == 403

    # У незаблокированного тот же вызов молчит
    other_id, _ = await _user(db, "student")
    await user_block_service.assert_not_blocked(db, other_id)


@pytest.mark.asyncio
async def test_blocking_is_closed_to_methodist(db, client):
    """Доступом распоряжается администратор, а не методист."""
    victim_id, _ = await _user(db, "student")
    _, methodist_token = await _user(db, "methodist")

    r = await client.post(f"/api/v1/users/{victim_id}/block", headers=_auth(methodist_token))
    assert r.status_code == 403, r.text
    assert await _blocked_at(db, victim_id) is None
