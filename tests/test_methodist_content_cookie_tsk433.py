"""tsk-433 Волна 1: контент-эндпоинты открыты cookie-методисту.

Дерево курсов и агрегаты статистики висели на legacy `get_db` (`APIKeyQuery` —
только `?api_key=` в query), поэтому веб-кабинет их дёрнуть не мог. Здесь
проверяем перевод на `get_current_user` + `require_role`:

- методист по cookie → 200;
- ученик по cookie → 403 (гейт реально закрывает, а не просто пускает всех);
- сервисный ключ → 200 (TG-боты продолжают работать — это главный риск правки).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _user_with_session(db, role: str | None) -> tuple[int, str]:
    """Создать пользователя с опциональной ролью и вернуть (id, bearer-токен)."""
    u = Users(
        email=f"t433-{role or 'norole'}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t433-{role or 'norole'}",
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


async def _course(db) -> int:
    c = Courses(
        title=f"t433-course-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
    )
    db.add(c)
    await db.flush()
    await db.commit()
    return c.id


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
    for cid in course_ids:
        await db.execute(text("DELETE FROM user_courses WHERE course_id=:c"), {"c": cid})
        await db.execute(text("DELETE FROM courses WHERE id=:c"), {"c": cid})
    await db.commit()


def _paths(course_id: int, task_id: int = 1) -> list[str]:
    """Эндпоинты, переведённые на cookie в рамках tsk-433."""
    return [
        "/api/v1/courses/roots",
        f"/api/v1/courses/{course_id}",
        f"/api/v1/courses/{course_id}/children",
        # `/courses/{id}/tree` намеренно НЕ здесь: он отдаёт 500 при любом гейте
        # (предсуществующий баг, tsk-463) и в Волну 1 не входит — навигация по
        # графу идёт через roots + children.
        #
        # `/courses/{id}/users` и `/courses/{id}/materials/stats` тоже НЕ здесь:
        # оставлены на legacy-гейте намеренно (ревью tsk-433). Первый отдаёт
        # персональные данные зачисленных и без course-ACL открыл бы их по любому
        # курсу; у второго нет потребителя в Волне 1.
        f"/api/v1/task-results/stats/by-course/{course_id}",
        f"/api/v1/task-results/stats/by-task/{task_id}",
    ]


@pytest.mark.asyncio
async def test_methodist_cookie_reads_content(db, client):
    uid, token = await _user_with_session(db, "methodist")
    cid = await _course(db)
    try:
        for path in _paths(cid):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
async def test_student_cookie_forbidden(db, client):
    """Ученик не должен видеть граф курсов и агрегаты целиком."""
    uid, token = await _user_with_session(db, "student")
    cid = await _course(db)
    try:
        for path in _paths(cid):
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403, f"{path} → {resp.status_code} {resp.text}"
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
async def test_service_key_still_works(db, client):
    """Главный риск правки: TG-боты ходят по сервисному ключу — не сломать их."""
    cid = await _course(db)
    api_key = next(iter(_settings.valid_api_keys))
    try:
        for path in _paths(cid):
            resp = await client.get(path, headers={"X-API-Key": api_key})
            assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
    finally:
        await _cleanup(db, [], [cid])


@pytest.mark.asyncio
async def test_anonymous_denied(db, client):
    """Без токена вовсе — не 200. Гейт начинается с аутентификации."""
    cid = await _course(db)
    try:
        for path in _paths(cid):
            resp = await client.get(path)
            assert resp.status_code in (401, 403), f"{path} → {resp.status_code}"
    finally:
        await _cleanup(db, [], [cid])


@pytest.mark.asyncio
async def test_pii_endpoints_stay_on_legacy_gate(db, client):
    """Без потребителя PII-путь остаётся закрытым.

    Исходно (Волна 1) сюда входил и `GET /courses/{id}/users`: он отдаёт
    email/ФИО/tg_id учеников, а потребителя тогда не было — держали закрытым.

    Аудит 2026-07-30 потребителя дал (блок «кто на курсе» в карточке), и путь
    переведён на гейт `methodist/admin` — намеренно у́же, чем предполагалось в
    Волне 1 («роль + course-ACL для teacher»): преподавателю состав чужого
    курса не нужен. Проверки на него живут теперь в
    `tests/test_tsk433_audit_fixes.py`, включая отказ преподавателю.

    Здесь остаётся то, у чего потребителя по-прежнему НЕТ.
    """
    uid, token = await _user_with_session(db, "methodist")
    cid = await _course(db)
    try:
        resp = await client.get(
            f"/api/v1/courses/{cid}/materials/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, f"materials/stats → {resp.status_code} {resp.text}"
    finally:
        await _cleanup(db, [uid], [cid])


@pytest.mark.asyncio
async def test_course_card_overrides_crud_and_404s(db, client):
    """`GET /courses/{id}` обслуживается override'ом из courses_extra, не CRUD.

    Признак: несуществующий курс отдаёт 404 (DomainError), а не 403 от APIKeyQuery.
    """
    uid, token = await _user_with_session(db, "methodist")
    try:
        resp = await client.get(
            "/api/v1/courses/999999999", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup(db, [uid], [])


@pytest.mark.asyncio
async def test_roots_not_shadowed_by_course_id(db, client):
    """`/courses/roots` объявлен до `/courses/{course_id}` — «roots» не должен

    попасть в обработчик карточки как id (иначе была бы 422).
    """
    uid, token = await _user_with_session(db, "methodist")
    try:
        resp = await client.get(
            "/api/v1/courses/roots", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)
    finally:
        await _cleanup(db, [uid], [])
