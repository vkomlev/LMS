"""tsk-433 Волна 2.3: структурные операции доступны кабинету методиста.

Порядок элементов, иерархия курсов и зависимости висели на legacy `get_db`
(`APIKeyQuery` — только `?api_key=` в query): ТГ-ботам доступны, браузеру по
cookie — нет. Здесь проверяем перевод на `require_role`:

- методист по cookie → работает;
- ученик по cookie → 403 (гейт закрывает, а не пускает всех);
- **сервисный ключ → по-прежнему работает** — это главный риск правки: боты
  ходят с `?api_key=` в адресе, и если бы `require_role` его не принимал,
  переводом мы сломали бы работающий канал.

Отдельно фиксируется решение оператора (2026-07-30): **перенос подкурса не
пересчитывает прогресс** — он привязан к материалам и заданиям, а те переезжают
вместе с узлом.
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
        email=f"t433s-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433s-{role or 'norole'}",
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


async def _course(db, title: str = "t433s") -> int:
    c = Courses(
        title=f"{title}-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
        course_uid=f"lms:test:t433s:{uuid.uuid4().hex[:12]}",
    )
    db.add(c)
    await db.flush()
    await db.commit()
    return c.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Иерархия курсов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_methodist_attaches_subcourse(db, client):
    """Методист по cookie привязывает подкурс к родителю."""
    parent = await _course(db, "parent")
    child = await _course(db, "child")
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [parent], "replace_parents": True},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text("SELECT COUNT(*) c FROM course_parents WHERE course_id=:c AND parent_course_id=:p"),
            {"c": child, "p": parent},
        )
    ).first()
    assert row.c == 1, "связь подкурс→родитель не создана"


@pytest.mark.asyncio
async def test_attach_second_parent_without_replace(db, client):
    """Режим «привязать к ещё одному родителю» не сносит первую связь."""
    p1 = await _course(db, "p1")
    p2 = await _course(db, "p2")
    child = await _course(db, "child")
    _, token = await _user_with_session(db, "methodist")

    await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [p1], "replace_parents": True},
        headers=_auth(token),
    )
    await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [p2], "replace_parents": False},
        headers=_auth(token),
    )

    row = (
        await db.execute(
            text("SELECT COUNT(*) c FROM course_parents WHERE course_id=:c"), {"c": child}
        )
    ).first()
    assert row.c == 2, "второй родитель должен добавиться к первому, а не заменить его"


@pytest.mark.asyncio
async def test_detach_makes_course_root(db, client):
    """Пустой список с заменой делает курс корневым."""
    parent = await _course(db, "parent")
    child = await _course(db, "child")
    _, token = await _user_with_session(db, "methodist")

    await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [parent], "replace_parents": True},
        headers=_auth(token),
    )
    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [], "replace_parents": True},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text("SELECT COUNT(*) c FROM course_parents WHERE course_id=:c"), {"c": child}
        )
    ).first()
    assert row.c == 0, "курс должен стать корневым"


@pytest.mark.asyncio
async def test_cycle_rejected(db, client):
    """Курс не может стать собственным предком — ловит триггер БД."""
    a = await _course(db, "a")
    b = await _course(db, "b")
    _, token = await _user_with_session(db, "methodist")

    await client.patch(
        f"/api/v1/courses/{b}/structure",
        json={"parent_course_ids": [a], "replace_parents": True},
        headers=_auth(token),
    )
    r = await client.patch(
        f"/api/v1/courses/{a}/structure",
        json={"parent_course_ids": [b], "replace_parents": True},
        headers=_auth(token),
    )
    assert r.status_code in (400, 409), (
        f"цикл в иерархии обязан отклоняться, получено {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_student_cannot_change_structure(db, client):
    parent = await _course(db, "parent")
    child = await _course(db, "child")
    _, token = await _user_with_session(db, "student")

    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [parent], "replace_parents": True},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_teacher_cannot_change_structure(db, client):
    """Преподаватель видит дерево, но не перестраивает его."""
    parent = await _course(db, "parent")
    child = await _course(db, "child")
    _, token = await _user_with_session(db, "teacher")

    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [parent], "replace_parents": True},
        headers=_auth(token),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Зависимости курсов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_methodist_manages_dependencies(db, client):
    """«ЕГЭ проходится после Python для ЕГЭ» — добавление и снятие."""
    course = await _course(db, "ege")
    required = await _course(db, "python-ege")
    _, token = await _user_with_session(db, "methodist")

    r = await client.post(
        f"/api/v1/courses/{course}/dependencies/{required}", headers=_auth(token)
    )
    assert r.status_code in (200, 201, 204), r.text

    listing = await client.get(
        f"/api/v1/courses/{course}/dependencies/", headers=_auth(token)
    )
    assert listing.status_code == 200, listing.text
    assert any(c["id"] == required for c in listing.json()), "зависимость не в списке"

    r = await client.delete(
        f"/api/v1/courses/{course}/dependencies/{required}", headers=_auth(token)
    )
    assert r.status_code in (200, 204), r.text


@pytest.mark.asyncio
async def test_teacher_reads_dependencies_but_cannot_change(db, client):
    """Преподавателю порядок прохождения виден, но менять его нельзя."""
    course = await _course(db, "ege")
    required = await _course(db, "python-ege")
    _, token = await _user_with_session(db, "teacher")

    listing = await client.get(
        f"/api/v1/courses/{course}/dependencies/", headers=_auth(token)
    )
    assert listing.status_code == 200, listing.text

    r = await client.post(
        f"/api/v1/courses/{course}/dependencies/{required}", headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_student_cannot_read_dependencies(db, client):
    course = await _course(db, "ege")
    _, token = await _user_with_session(db, "student")
    r = await client.get(f"/api/v1/courses/{course}/dependencies/", headers=_auth(token))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Совместимость с ботами — главный риск перевода гейтов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_key_still_works_everywhere(db, client):
    """ТГ-боты ходят с `?api_key=` в адресе — после перевода это обязано работать.

    Если бы `require_role` не принимал legacy-ключ, перевод гейтов молча
    сломал бы работающий канал методиста в Telegram.
    """
    parent = await _course(db, "parent")
    child = await _course(db, "child")
    required = await _course(db, "req")
    key = {"api_key": _api_key()}

    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        params=key,
        json={"parent_course_ids": [parent], "replace_parents": True},
    )
    assert r.status_code == 200, f"структура по сервисному ключу: {r.text}"

    r = await client.get(f"/api/v1/courses/{child}/dependencies/", params=key)
    assert r.status_code == 200, f"чтение зависимостей по сервисному ключу: {r.text}"

    r = await client.post(
        f"/api/v1/courses/{child}/dependencies/{required}", params=key
    )
    assert r.status_code in (200, 201, 204), (
        f"запись зависимости по сервисному ключу: {r.status_code} {r.text}"
    )


@pytest.mark.asyncio
async def test_reorder_endpoints_reachable_by_methodist(db, client):
    """Порядок материалов и заданий доступен методисту по cookie."""
    course = await _course(db, "reorder")
    _, token = await _user_with_session(db, "methodist")

    r = await client.post(
        f"/api/v1/courses/{course}/materials/reorder",
        json=[],
        headers=_auth(token),
    )
    assert r.status_code in (200, 422), f"неожиданный отказ гейта: {r.status_code} {r.text}"
    assert r.status_code != 403, "методист обязан иметь доступ к порядку материалов"

    r = await client.post(
        f"/api/v1/courses/{course}/tasks/reorder",
        json={"items": []},
        headers=_auth(token),
    )
    assert r.status_code != 403, "методист обязан иметь доступ к порядку заданий"


@pytest.mark.asyncio
async def test_reorder_denied_for_student(db, client):
    course = await _course(db, "reorder")
    _, token = await _user_with_session(db, "student")
    r = await client.post(
        f"/api/v1/courses/{course}/materials/reorder", json=[], headers=_auth(token)
    )
    assert r.status_code == 403
