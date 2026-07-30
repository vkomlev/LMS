"""tsk-433 Волна 3: чтение людей открыто кабинету методиста.

Здесь впервые в задаче — **персональные данные**: почта, имя, идентификатор в
Telegram. Поэтому гейт уже, чем у контента:

- методист и админ — да (роли, которые и так работают со всеми учениками);
- **преподаватель — нет**: общий список ему не нужен, у него есть свой ростер
  `GET /users/{teacher_id}/students`;
- ученик — нет;
- сервисный ключ — да, иначе сломался бы ТГ-бот методиста, для которого это
  основной экран людей.

Отдельно фиксируется решение оператора (2026-07-30): **роли методист не
меняет**, только читает. Назначение роли — повышение привилегий, оно остаётся
за админом; ТГ-бот методиста тоже ролями не управляет.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()

#: Пути чтения людей, переведённые на cookie + роль в этой волне.
READ_PATHS = [
    "/api/v1/users/?limit=5",
    "/api/v1/users/search?q=тест&limit=5",
]


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t433p-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433p-{role or 'norole'}",
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


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.asyncio
async def test_methodist_reads_people(db, client, path):
    _, token = await _user(db, "methodist")
    r = await client.get(path, headers=_auth(token))
    assert r.status_code == 200, f"{path}: {r.text}"


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.asyncio
async def test_student_cannot_read_people(db, client, path):
    """Ученик не должен видеть чужие персональные данные."""
    _, token = await _user(db, "student")
    r = await client.get(path, headers=_auth(token))
    assert r.status_code == 403, f"{path} пустил ученика: {r.status_code}"


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.asyncio
async def test_teacher_cannot_read_all_people(db, client, path):
    """Преподавателю общий список не открыт — у него свой ростер.

    Это сознательное сужение: роль teacher видит своих учеников через
    `/users/{teacher_id}/students`, а полный список школы ей не нужен.
    """
    _, token = await _user(db, "teacher")
    r = await client.get(path, headers=_auth(token))
    assert r.status_code == 403, f"{path} пустил преподавателя: {r.status_code}"


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.asyncio
async def test_anonymous_cannot_read_people(client, path):
    r = await client.get(path)
    assert r.status_code == 401, f"{path} без сессии: {r.status_code}"


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.asyncio
async def test_service_key_still_reads_people(client, path):
    """ТГ-бот методиста ходит с ключом в адресе — это его основной экран людей."""
    sep = "&" if "?" in path else "?"
    r = await client.get(f"{path}{sep}api_key={_api_key()}")
    assert r.status_code == 200, f"{path} по сервисному ключу: {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_methodist_reads_student_links_and_courses(db, client):
    """Связи ученик↔преподаватель и курсы человека доступны методисту."""
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    for path in (
        f"/api/v1/users/{student_id}/teachers",
        f"/api/v1/users/{student_id}/courses",
        f"/api/v1/users/{student_id}/roles/",
    ):
        r = await client.get(path, headers=_auth(token))
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_student_cannot_read_someone_links(db, client):
    other_id, _ = await _user(db, "student")
    _, token = await _user(db, "student")
    r = await client.get(f"/api/v1/users/{other_id}/teachers", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_roles_are_read_only_for_methodist(db, client):
    """Методист видит роли, но не назначает их (решение оператора 2026-07-30).

    Назначение роли — повышение привилегий: иначе методист смог бы выдать роль
    себе или кому угодно. Эндпоинт записи остаётся за прежним гейтом.
    """
    victim_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    read = await client.get(f"/api/v1/users/{victim_id}/roles/", headers=_auth(token))
    assert read.status_code == 200, read.text

    # роль methodist = 2; попытка выдать её по cookie-сессии не должна проходить
    write = await client.post(f"/api/v1/users/{victim_id}/roles/2", headers=_auth(token))
    assert write.status_code in (401, 403), (
        f"методист не должен назначать роли, получено {write.status_code}"
    )
