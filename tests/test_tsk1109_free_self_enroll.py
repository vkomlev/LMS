"""tsk-1109: самозапись ученика на бесплатный курс.

`POST /api/v1/me/courses/{course_id}/enroll-free`. До правки записывать в
`user_courses` умели только методист и админ, и вошедший с лендинга бесплатного
курса попадал в пустой кабинет.

Проверяем на настоящей БД:
- бесплатный корневой курс — запись создаётся, событие аудита пишется;
- повтор — 200 `created=false`, вторая связь и второе событие не появляются;
- платный / «не продаётся» / без цены — 403, связи нет;
- тема курса (есть родитель), даже бесплатная — 403;
- выключенный курс — 409; приостановленная сотрудником связь — 409 и не включается;
- несуществующий курс — 404; без входа — 401; частые запросы — 429.
"""
from __future__ import annotations

import random

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.v1 import me as me_module
from app.models.users import Users
from app.services.audit_service import STUDENT_COURSE_SELF_ENROLLED
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk1109"


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _cleanup(db):
    """Убрать тестовые курсы, группы и сессии учеников (связи уходят каскадом)."""
    yield
    await db.execute(text("DELETE FROM courses WHERE title LIKE :p"), {"p": f"{_TAG}-%"})
    await db.execute(text("DELETE FROM pricing_group WHERE name LIKE :p"), {"p": f"{_TAG}-%"})
    for tbl in ("user_session", "identity_link"):
        await db.execute(
            text(
                f"DELETE FROM {tbl} WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE :p)"
            ),
            {"p": f"{_TAG}-%"},
        )
    # Самих учеников не удаляем: на них ссылается журнал аудита, а он только
    # на дозапись (триггер). Их подбирает общая уборка conftest.
    await db.commit()


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Лимит частоты проверяем отдельным тестом; остальным Redis не нужен."""

    async def _never(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(me_module, "is_rate_limited", _never)


async def _student(db) -> tuple[int, dict[str, str]]:
    email = f"{_TAG}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="Тестов Ученик", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, {"Authorization": f"Bearer {token}"}


async def _course(
    db, *, sale_status: str | None, is_active: bool = True, parent_id: int | None = None
) -> int:
    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, is_active, course_uid) "
                "VALUES (:t, 'self_guided', :a, :u) RETURNING id"
            ),
            {
                "t": f"{_TAG}-{random.randint(10**6, 10**7)}",
                "a": is_active,
                "u": f"test:{_TAG}:{random.randint(10**6, 10**7)}",
            },
        )
    ).scalar()
    group_id = None
    if sale_status == "paid":
        group_id = (
            await db.execute(
                text("INSERT INTO pricing_group (name) VALUES (:n) RETURNING id"),
                {"n": f"{_TAG}-{random.randint(10**6, 10**7)}"},
            )
        ).scalar()
    if sale_status is not None:
        await db.execute(
            text(
                "INSERT INTO course_pricing (course_id, sale_status, group_id) "
                "VALUES (:c, :s, :g)"
            ),
            {"c": course_id, "s": sale_status, "g": group_id},
        )
    if parent_id is not None:
        await db.execute(
            text(
                "INSERT INTO course_parents (course_id, parent_course_id) VALUES (:c, :p)"
            ),
            {"c": course_id, "p": parent_id},
        )
    await db.commit()
    return course_id


async def _link(db, user_id: int, course_id: int) -> bool | None:
    return (
        await db.execute(
            text("SELECT is_active FROM user_courses WHERE user_id = :u AND course_id = :c"),
            {"u": user_id, "c": course_id},
        )
    ).scalar_one_or_none()


async def _audit_count(db, user_id: int, course_id: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type = :e AND user_id = :u "
                    "  AND (details->>'course_id')::int = :c"
                ),
                {"e": STUDENT_COURSE_SELF_ENROLLED, "u": user_id, "c": course_id},
            )
        ).scalar_one()
    )


def _url(course_id: int) -> str:
    return f"/api/v1/me/courses/{course_id}/enroll-free"


@pytest.mark.asyncio
async def test_free_root_course_enrolls_and_is_idempotent(db, client):
    user_id, headers = await _student(db)
    course_id = await _course(db, sale_status="free")

    first = await client.post(_url(course_id), headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["course_id"] == course_id
    assert body["created"] is True
    assert body["course_uid"].startswith(f"test:{_TAG}:")
    assert await _link(db, user_id, course_id) is True
    assert await _audit_count(db, user_id, course_id) == 1

    second = await client.post(_url(course_id), headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert await _audit_count(db, user_id, course_id) == 1

    # Курс виден ученику там же, где назначенный методистом.
    mine = await client.get("/api/v1/me/courses", headers=headers)
    assert mine.status_code == 200, mine.text
    assert course_id in {c["course_id"] for c in mine.json()}


@pytest.mark.asyncio
@pytest.mark.parametrize("sale_status", ["paid", "not_for_sale", None])
async def test_not_free_course_is_forbidden(db, client, sale_status):
    user_id, headers = await _student(db)
    course_id = await _course(db, sale_status=sale_status)

    resp = await client.post(_url(course_id), headers=headers)
    assert resp.status_code == 403, resp.text
    assert await _link(db, user_id, course_id) is None


@pytest.mark.asyncio
async def test_free_topic_with_parent_is_forbidden(db, client):
    user_id, headers = await _student(db)
    root_id = await _course(db, sale_status="paid")
    topic_id = await _course(db, sale_status="free", parent_id=root_id)

    resp = await client.post(_url(topic_id), headers=headers)
    assert resp.status_code == 403, resp.text
    assert await _link(db, user_id, topic_id) is None


@pytest.mark.asyncio
async def test_inactive_free_course_is_conflict(db, client):
    user_id, headers = await _student(db)
    course_id = await _course(db, sale_status="free", is_active=False)

    resp = await client.post(_url(course_id), headers=headers)
    assert resp.status_code == 409, resp.text
    assert await _link(db, user_id, course_id) is None


@pytest.mark.asyncio
async def test_paused_link_is_not_reactivated(db, client):
    user_id, headers = await _student(db)
    course_id = await _course(db, sale_status="free")
    await db.execute(
        text("INSERT INTO user_courses (user_id, course_id, is_active) VALUES (:u, :c, false)"),
        {"u": user_id, "c": course_id},
    )
    await db.commit()

    resp = await client.post(_url(course_id), headers=headers)
    assert resp.status_code == 409, resp.text
    assert await _link(db, user_id, course_id) is False
    assert await _audit_count(db, user_id, course_id) == 0


@pytest.mark.asyncio
async def test_missing_course_is_404(db, client):
    _, headers = await _student(db)
    resp = await client.post(_url(2_000_000_000), headers=headers)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_anonymous_is_401(db, client):
    course_id = await _course(db, sale_status="free")
    resp = await client.post(_url(course_id))
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_rate_limited_is_429(db, client, monkeypatch):
    user_id, headers = await _student(db)
    course_id = await _course(db, sale_status="free")

    seen: list[str] = []

    async def _always(_redis, key, **_kwargs) -> bool:
        seen.append(key)
        return True

    monkeypatch.setattr(me_module, "is_rate_limited", _always)
    resp = await client.post(_url(course_id), headers=headers)
    assert resp.status_code == 429, resp.text
    assert seen == [f"enroll_free:user:{user_id}"]
    assert await _link(db, user_id, course_id) is None


async def _depend(db, course_id: int, required_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) VALUES (:c, :r)"
        ),
        {"c": course_id, "r": required_id},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_free_course_with_paid_dependency_is_forbidden(db, client):
    """Доназначение зависимостей не должно открыть платный курс даром."""
    user_id, headers = await _student(db)
    paid_id = await _course(db, sale_status="paid")
    free_id = await _course(db, sale_status="free")
    await _depend(db, free_id, paid_id)

    resp = await client.post(_url(free_id), headers=headers)
    assert resp.status_code == 403, resp.text
    assert await _link(db, user_id, free_id) is None
    assert await _link(db, user_id, paid_id) is None


@pytest.mark.asyncio
async def test_free_course_with_free_dependency_enrolls_both(db, client):
    user_id, headers = await _student(db)
    base_id = await _course(db, sale_status="free")
    free_id = await _course(db, sale_status="free")
    await _depend(db, free_id, base_id)

    resp = await client.post(_url(free_id), headers=headers)
    assert resp.status_code == 200, resp.text
    assert await _link(db, user_id, free_id) is True
    assert await _link(db, user_id, base_id) is True
