"""tsk-433 Волна 2.1: правка материала методистом по cookie.

Generic CRUD `PUT /materials/{id}` сидит на legacy `get_db` (`?api_key=` в
query) — браузеру недоступен. Здесь проверяем добавленный
`PATCH /materials/{material_id}`:

- методист по cookie правит и получает пометку ручной правки;
- ученик по cookie → 403 (гейт закрывает, а не пускает всех);
- без сессии → 401;
- пометка снимается через `DELETE /materials/{id}/manual-edit`;
- ключи сопоставления с источником (`type`, `course_id`, `external_uid`)
  правкой из веба не меняются — их в схеме патча просто нет.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.courses import Courses
from app.models.materials import Materials
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
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


async def _material(db) -> tuple[int, int]:
    c = Courses(
        title=f"t433p-course-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
    )
    db.add(c)
    await db.flush()
    m = Materials(
        course_id=c.id,
        title="Из источника",
        type="text",
        content={"text": "<p>исходное</p>", "format": "html"},
        external_uid=f"wp:t433p:{random.randint(10**8, 10**10)}",
    )
    db.add(m)
    await db.flush()
    await db.commit()
    return c.id, m.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_methodist_patches_material_and_gets_provenance(db, client):
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/materials/{mid}",
        json={"title": "Поправлено методистом"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Поправлено методистом"

    row = (
        await db.execute(
            text("SELECT title, content_provenance FROM materials WHERE id=:i"), {"i": mid}
        )
    ).first()
    assert row.title == "Поправлено методистом"
    assert row.content_provenance is not None, "правка обязана оставить пометку"
    assert row.content_provenance["source"] == "manual_web"
    assert row.content_provenance["fields"] == ["title"]


@pytest.mark.asyncio
async def test_provenance_returned_to_client(db, client):
    """Пометка приходит в ответе API, а не только лежит в БД.

    Механизм, которого не видно, для методиста не существует: правка выглядела
    бы обычной, а материал тихо выпал бы из-под управления источника. Первый
    живой прогон поймал ровно это — колонка заполнялась, но в `MaterialRead`
    поля не было, и признак на экране не появлялся.
    """
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    patched = await client.patch(
        f"/api/v1/materials/{mid}", json={"title": "правка"}, headers=_auth(token)
    )
    assert patched.status_code == 200, patched.text
    assert patched.json().get("content_provenance", {}).get("source") == "manual_web", (
        "PATCH обязан вернуть пометку клиенту"
    )

    fetched = await client.get(f"/api/v1/materials/{mid}", headers=_auth(token))
    assert fetched.status_code == 200, fetched.text
    prov = fetched.json().get("content_provenance")
    assert prov is not None and prov["fields"] == ["title"], (
        "GET обязан отдавать пометку — по ней рисуется признак в кабинете"
    )


@pytest.mark.asyncio
async def test_student_cannot_patch(db, client):
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "student")

    r = await client.patch(
        f"/api/v1/materials/{mid}", json={"title": "взлом"}, headers=_auth(token)
    )
    assert r.status_code == 403, f"ученик не должен править материалы, получено {r.status_code}"


@pytest.mark.asyncio
async def test_anonymous_cannot_patch(db, client):
    _, mid = await _material(db)
    r = await client.patch(f"/api/v1/materials/{mid}", json={"title": "взлом"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_service_fields_do_not_set_provenance(db, client):
    """Активность и порядок пометки не ставят — импорт их и так не трогает."""
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/materials/{mid}", json={"is_active": False}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text("SELECT is_active, content_provenance FROM materials WHERE id=:i"), {"i": mid}
        )
    ).first()
    assert row.is_active is False
    assert row.content_provenance is None, (
        "служебные поля защищены механизмом tsk-377/378, провенанс им не нужен"
    )


@pytest.mark.asyncio
async def test_repeated_patches_accumulate_fields(db, client):
    """Вторая правка другого поля не стирает пометку с первого."""
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    await client.patch(f"/api/v1/materials/{mid}", json={"title": "раз"}, headers=_auth(token))
    await client.patch(
        f"/api/v1/materials/{mid}",
        json={"content": {"text": "<p>два</p>", "format": "html"}},
        headers=_auth(token),
    )

    row = (
        await db.execute(
            text("SELECT content_provenance FROM materials WHERE id=:i"), {"i": mid}
        )
    ).first()
    assert row.content_provenance["fields"] == ["content", "title"]


@pytest.mark.asyncio
async def test_clear_manual_edit_removes_provenance(db, client):
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    await client.patch(f"/api/v1/materials/{mid}", json={"title": "правка"}, headers=_auth(token))
    r = await client.delete(f"/api/v1/materials/{mid}/manual-edit", headers=_auth(token))
    assert r.status_code == 200, r.text

    row = (
        await db.execute(
            text("SELECT title, content_provenance FROM materials WHERE id=:i"), {"i": mid}
        )
    ).first()
    assert row.content_provenance is None, "пометка обязана сняться"
    assert row.title == "правка", (
        "снятие пометки не откатывает текст — прежний вернёт источник при переиздании"
    )


@pytest.mark.asyncio
async def test_broken_content_rejected(db, client):
    """Структура content валидируется по типу материала, а не принимается как есть."""
    _, mid = await _material(db)
    _, token = await _user_with_session(db, "methodist")

    r = await client.patch(
        f"/api/v1/materials/{mid}",
        json={"content": {"unexpected": "структура не для text"}},
        headers=_auth(token),
    )
    assert r.status_code == 422, f"ожидали 422 на битой структуре, получено {r.status_code}"
