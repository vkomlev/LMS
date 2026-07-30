"""tsk-433 Волна 3.2: записи по людям открыты кабинету методиста.

Что здесь проверяется и почему именно это:

- **все восемь путей записи**, а не выборка. Урок Волны 3.1: набор путей,
  составленный по памяти, дважды терял по эндпоинту, и тесты этого не ловили,
  потому что писались по тому же набору. Здесь список собран механически из
  роутеров (`@router.post|delete|patch`);
- **отчисление живёт под другим префиксом** (`/user-courses/...`, не рядом с
  зачислением) — отдельный тест, чтобы путь снова не выпал из виду;
- **`tg_id` методист не меняет**: это идентификатор входа через бота, смена из
  кабинета = передача доступа к чужому аккаунту (решение оператора 2026-07-30).
  Сервисный ключ менять его по-прежнему может — им пользуются боты;
- **зачисление только на курс верхнего уровня**: триггер БД
  `check_user_course_has_no_parents` запрещает вложенный, и без обработки
  методист получал бы 500 вместо объяснения;
- **занятая почта** — 409, а не 500 (частичный уникальный индекс на
  `users.email`).
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


async def _user(db, role: str | None, email: str | None = None) -> tuple[int, str]:
    u = Users(
        email=email or f"t433w-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433w-{role or 'norole'}",
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


async def _root_course(db) -> int:
    """Курс верхнего уровня — единственный, на который можно зачислять."""
    row = await db.execute(
        text(
            "INSERT INTO courses (title, course_uid, access_level) "
            "VALUES (:t, :u, 'self_guided') RETURNING id"
        ),
        {
            "t": f"t433w курс {random.randint(10**6, 10**8)}",
            "u": f"t433w-{random.randint(10**8, 10**10)}",
        },
    )
    course_id = row.scalar_one()
    await db.commit()
    return course_id


async def _child_course(db, parent_id: int) -> int:
    """Вложенный курс — на такой зачислять нельзя (проверяет триггер БД)."""
    child_id = await _root_course(db)
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, 1)"
        ),
        {"c": child_id, "p": parent_id},
    )
    await db.commit()
    return child_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Привязка ученик ↔ преподаватель
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_links_and_unlinks_teacher(db, client):
    student_id, _ = await _user(db, "student")
    teacher_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")

    add = await client.post(
        f"/api/v1/users/{student_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert add.status_code == 204, add.text

    listed = await client.get(f"/api/v1/users/{student_id}/teachers", headers=_auth(token))
    assert teacher_id in [t["id"] for t in listed.json()]

    drop = await client.delete(
        f"/api/v1/users/{student_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert drop.status_code == 204, drop.text

    listed2 = await client.get(f"/api/v1/users/{student_id}/teachers", headers=_auth(token))
    assert teacher_id not in [t["id"] for t in listed2.json()]


@pytest.mark.asyncio
async def test_teacher_cannot_link_himself_to_student(db, client):
    """Преподаватель не раздаёт себе учеников — распределяет методист."""
    student_id, _ = await _user(db, "student")
    teacher_id, token = await _user(db, "teacher")

    r = await client.post(
        f"/api/v1/users/{student_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_student_cannot_link_teacher(db, client):
    student_id, token = await _user(db, "student")
    teacher_id, _ = await _user(db, "teacher")

    r = await client.post(
        f"/api/v1/users/{student_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_key_still_links(db, client):
    """ТГ-бот методиста распределяет учеников тем же путём."""
    student_id, _ = await _user(db, "student")
    teacher_id, _ = await _user(db, "teacher")

    r = await client.post(
        f"/api/v1/users/{student_id}/teachers/{teacher_id}?api_key={_api_key()}"
    )
    assert r.status_code == 204, r.text


# --------------------------------------------------------------------------
# Зачисление и отчисление
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_enrolls_and_unenrolls(db, client):
    student_id, _ = await _user(db, "student")
    course_id = await _root_course(db)
    _, token = await _user(db, "methodist")

    enroll = await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course_id]},
        headers=_auth(token),
    )
    assert enroll.status_code == 201, enroll.text

    listed = await client.get(
        f"/api/v1/users/{student_id}/courses?role=student", headers=_auth(token)
    )
    assert course_id in [c["course_id"] for c in listed.json()["courses"]]

    # Отчисление живёт под ДРУГИМ префиксом — отдельный роутер `/user-courses`.
    drop = await client.delete(
        f"/api/v1/user-courses/{student_id}/{course_id}", headers=_auth(token)
    )
    assert drop.status_code == 204, drop.text

    listed2 = await client.get(
        f"/api/v1/users/{student_id}/courses?role=student", headers=_auth(token)
    )
    assert course_id not in [c["course_id"] for c in listed2.json()["courses"]]


@pytest.mark.asyncio
async def test_enroll_on_nested_course_is_explained_not_500(db, client):
    """Вложенный курс — понятный отказ 409, а не голая ошибка базы.

    Ученику выдаётся курс верхнего уровня, главы внутри открывает движок.
    Триггер БД это гарантирует; задача эндпоинта — объяснить, а не упасть.
    """
    parent_id = await _root_course(db)
    child_id = await _child_course(db, parent_id)
    student_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [child_id]},
        headers=_auth(token),
    )
    assert r.status_code == 409, f"ожидали объяснение, получили {r.status_code}: {r.text}"
    assert "верхнего уровня" in r.json()["detail"]


@pytest.mark.asyncio
async def test_student_cannot_enroll_himself(db, client):
    student_id, token = await _user(db, "student")
    course_id = await _root_course(db)

    r = await client.post(
        f"/api/v1/users/{student_id}/courses/bulk",
        json={"course_ids": [course_id]},
        headers=_auth(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_student_cannot_unenroll_anyone(db, client):
    other_id, _ = await _user(db, "student")
    course_id = await _root_course(db)
    _, token = await _user(db, "student")

    r = await client.delete(
        f"/api/v1/user-courses/{other_id}/{course_id}", headers=_auth(token)
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_key_still_enrolls(db, client):
    student_id, _ = await _user(db, "student")
    course_id = await _root_course(db)

    r = await client.post(
        f"/api/v1/users/{student_id}/courses/bulk?api_key={_api_key()}",
        json={"course_ids": [course_id]},
    )
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------
# Закрепление преподавателя за курсом
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_key_still_unenrolls(db, client):
    """Отчисление ботом — отдельная проверка: путь под другим префиксом.

    Проверяется вместе с зачислением, но НЕ вместо него: их обслуживают разные
    роутеры, и «работает bulk» ничего не говорит про `/user-courses`.
    """
    student_id, _ = await _user(db, "student")
    course_id = await _root_course(db)
    await client.post(
        f"/api/v1/users/{student_id}/courses/bulk?api_key={_api_key()}",
        json={"course_ids": [course_id]},
    )

    r = await client.delete(
        f"/api/v1/user-courses/{student_id}/{course_id}?api_key={_api_key()}"
    )
    assert r.status_code == 204, r.text


@pytest.mark.asyncio
async def test_service_key_still_assigns_course_teacher(db, client):
    """Закрепление преподавателя ботом — третий роутер, третья проверка."""
    teacher_id, _ = await _user(db, "teacher")
    course_id = await _root_course(db)

    add = await client.post(
        f"/api/v1/courses/{course_id}/teachers/{teacher_id}?api_key={_api_key()}"
    )
    assert add.status_code == 204, add.text

    drop = await client.delete(
        f"/api/v1/courses/{course_id}/teachers/{teacher_id}?api_key={_api_key()}"
    )
    assert drop.status_code == 204, drop.text


@pytest.mark.asyncio
async def test_methodist_assigns_and_removes_course_teacher(db, client):
    teacher_id, _ = await _user(db, "teacher")
    course_id = await _root_course(db)
    _, token = await _user(db, "methodist")

    add = await client.post(
        f"/api/v1/courses/{course_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert add.status_code == 204, add.text

    listed = await client.get(
        f"/api/v1/courses/{course_id}/teachers", headers=_auth(token)
    )
    assert teacher_id in [t["id"] for t in listed.json()["items"]]

    drop = await client.delete(
        f"/api/v1/courses/{course_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert drop.status_code == 204, drop.text


@pytest.mark.asyncio
async def test_assign_teacher_to_nested_course_is_explained(db, client):
    """И здесь вложенный курс объясняется, а не падает.

    Отдельная проверка, а не «то же самое, что у ученика»: это другая таблица
    и другой триггер (`check_course_has_no_parents` на `teacher_courses`).
    """
    parent_id = await _root_course(db)
    child_id = await _child_course(db, parent_id)
    teacher_id, _ = await _user(db, "teacher")
    _, token = await _user(db, "methodist")

    r = await client.post(
        f"/api/v1/courses/{child_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 409, f"ожидали объяснение, получили {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_teacher_cannot_assign_himself_to_course(db, client):
    teacher_id, token = await _user(db, "teacher")
    course_id = await _root_course(db)

    r = await client.post(
        f"/api/v1/courses/{course_id}/teachers/{teacher_id}", headers=_auth(token)
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Правка карточки
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_methodist_edits_name_and_email(db, client):
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"full_name": "Иванов Иван", "email": f"fixed{target_id}@example.com"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Иванов Иван"


@pytest.mark.asyncio
async def test_methodist_cannot_change_tg_id(db, client):
    """Идентификатор Telegram — ключ входа, из кабинета не меняется."""
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}",
        json={"tg_id": 424242},
        headers=_auth(token),
    )
    assert r.status_code == 403, f"tg_id не должен меняться из кабинета: {r.text}"


@pytest.mark.asyncio
async def test_service_key_can_still_change_tg_id(db, client):
    """Боты привязывают Telegram сами — им путь остаётся открыт."""
    target_id, _ = await _user(db, "student")

    r = await client.patch(
        f"/api/v1/users/{target_id}?api_key={_api_key()}",
        json={"tg_id": random.randint(10**8, 10**9)},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_taken_email_is_explained_not_500(db, client):
    """Занятая почта — понятный отказ, а не ошибка базы."""
    taken = f"taken{random.randint(10**8, 10**10)}@example.com"
    await _user(db, "student", email=taken)
    target_id, _ = await _user(db, "student")
    _, token = await _user(db, "methodist")

    r = await client.patch(
        f"/api/v1/users/{target_id}", json={"email": taken}, headers=_auth(token)
    )
    assert r.status_code == 409, f"ожидали объяснение, получили {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_student_cannot_edit_someone_card(db, client):
    other_id, _ = await _user(db, "student")
    _, token = await _user(db, "student")

    r = await client.patch(
        f"/api/v1/users/{other_id}", json={"full_name": "взлом"}, headers=_auth(token)
    )
    assert r.status_code == 403
