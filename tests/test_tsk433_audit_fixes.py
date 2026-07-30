"""tsk-433, фиксы по сводному аудиту 2026-07-30.

Здесь закрываются два пробела, найденные аудитом функциональной полноты:

- **правка карточки курса** — курс оставался единственной сущностью кабинета,
  которую нельзя было править (у материала и задания правка с Волны 2);
- **состав курса** — «кто на нём учится» не был виден ниоткуда: связь
  показывалась только со стороны человека.

Оба пути заведены осознанно узко:

- `PATCH /courses/{id}/card` — ОТДЕЛЬНЫЙ адрес, а не перекрытие общего
  `PATCH /courses/{id}`: у общего шире контракт (в т.ч. структура графа), и
  подмена его узкой схемой урезала бы возможности ТГ-ботов;
- `GET /courses/{id}/users` — гейт `methodist/admin`, БЕЗ преподавателя: путь
  отдаёт почты и ФИО всех зачисленных, а свой ростер преподаватель берёт
  через `/users/{teacher_id}/students`.
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


def _api_key() -> str:
    return next(iter(_settings.valid_api_keys))


async def _user(db, role: str | None) -> tuple[int, str]:
    u = Users(
        email=f"t433a-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433a-{role or 'norole'}",
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


async def _course(db, title: str = "t433a курс") -> tuple[int, str]:
    uid = f"t433a-{random.randint(10**8, 10**10)}"
    row = await db.execute(
        text(
            "INSERT INTO courses (title, course_uid, access_level) "
            "VALUES (:t, :u, 'self_guided') RETURNING id"
        ),
        {"t": f"{title} {random.randint(10**6, 10**8)}", "u": uid},
    )
    course_id = row.scalar_one()
    await db.commit()
    return course_id, uid


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Правка карточки курса
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_edits_course_card(db, client):
    course_id, _ = await _course(db)
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"title": "Python для ЕГЭ (правлено)", "access_level": "manual_check"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Python для ЕГЭ (правлено)"
    assert body["access_level"] == "manual_check"


@pytest.mark.asyncio
async def test_course_card_patch_is_partial(db, client):
    """Не переданное поле не затирается — как у материала и задания."""
    course_id, uid = await _course(db, title="Исходное имя")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"description": "только описание"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["course_uid"] == uid
    assert r.json()["title"].startswith("Исходное имя")


@pytest.mark.asyncio
async def test_taken_course_uid_is_explained_not_500(db, client):
    """Занятый код курса — понятный отказ, а не ошибка базы."""
    _, taken_uid = await _course(db)
    course_id, _ = await _course(db)
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"course_uid": taken_uid},
        headers=_auth(token),
    )
    assert r.status_code == 409, f"ожидали объяснение, получили {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_teacher_cannot_edit_course_card(db, client):
    """Правка содержания курса — дело методиста, не преподавателя."""
    course_id, _ = await _course(db)
    _, token = await _user(db, "teacher")

    r = await client.patch(
        f"/api/v1/courses/{course_id}/card",
        json={"title": "нельзя"},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_missing_course_card_is_404(db, client):
    _, token = await _user(db, "methodist")
    r = await client.patch(
        "/api/v1/courses/999999999/card", json={"title": "x"}, headers=_auth(token)
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_general_course_patch_still_works_by_service_key(db, client):
    """Общий PATCH не тронут: у него шире контракт, им ходят боты.

    Ради этого правка карточки и заведена отдельным адресом, а не перекрытием.
    """
    course_id, _ = await _course(db)

    r = await client.patch(
        f"/api/v1/courses/{course_id}?api_key={_api_key()}",
        json={"title": "правка ботом"},
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------
# Состав курса
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_sees_course_members(db, client):
    course_id, _ = await _course(db)
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course_id]},
        headers=_auth(token),
    )

    r = await client.get(f"/api/v1/courses/{course_id}/users", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert student_id in [u["user_id"] for u in r.json()["users"]]


@pytest.mark.asyncio
async def test_teacher_cannot_see_course_members(db, client):
    """Состав чужого курса преподавателю не открыт — там персональные данные.

    То же сужение, что по спискам людей в Волне 3.1: свой ростер преподаватель
    берёт через `/users/{teacher_id}/students`.
    """
    course_id, _ = await _course(db)
    _, token = await _user(db, "teacher")

    r = await client.get(f"/api/v1/courses/{course_id}/users", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_student_cannot_see_course_members(db, client):
    course_id, _ = await _course(db)
    _, token = await _user(db, "student")

    r = await client.get(f"/api/v1/courses/{course_id}/users", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_key_still_sees_course_members(db, client):
    """ТГ-бот методиста показывает состав курса — путь ему остаётся открыт."""
    course_id, _ = await _course(db)

    r = await client.get(f"/api/v1/courses/{course_id}/users?api_key={_api_key()}")
    assert r.status_code == 200, r.text
