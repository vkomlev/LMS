"""tsk-127 (S3): регресс-тест авторизации GET /api/v1/courses/by-code/{code}.

Контекст: раньше эндпоинт висел на `Depends(get_db)` (требовал legacy
service api-key) и отдавал ученику 403. tsk-127 перевёл его на
`get_current_user` + `get_bare_db` — доступен любому аутентифицированному
пользователю (cookie ученика ИЛИ сервисный ключ). От этого фикса зависит
SPW self-heal `CourseNotFoundResolver` (резолв листового course_uid → id →
корень), чинивший баг «Курс не найден».

Регресс защищает от случайного возврата авторизации на service-key gate:
1. Студенческая cookie-сессия → 200 + CourseRead (правильные id и course_uid).
2. Анонимный запрос (без cookie, без api_key) → 401.
3. Сервисный ключ (?api_key=) → 200 — обратная совместимость для service-вызовов.
4. Несуществующий course_uid под валидной сессией → 404.
"""
from __future__ import annotations

import random
from typing import AsyncGenerator, Tuple

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()
_API_KEY: str = next(iter(_settings.valid_api_keys))


async def _setup_student_session(db: AsyncSession) -> Tuple[int, str]:
    """Создать ученика с email-identity и сессией.

    :param db: асинхронная сессия БД.
    :return: кортеж (user_id, session_token). Токен годен и как cookie
        `session`, и как Bearer — здесь используется именно cookie-путь,
        как у браузерного SPW.
    """
    email = f"bycode_{random.randint(10**8, 10**10)}@example.com"
    user = Users(email=email, password_hash=None, full_name="tsk127-bycode", tg_id=None)
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", email)
    token, _, _ = await create_session(db, user_id=user.id)
    await db.commit()
    return user.id, token


async def _cleanup_user(db: AsyncSession, user_id: int) -> None:
    """Снять сессии и identity ученика (без DELETE FROM users — FK на audit_event).

    :param db: асинхронная сессия БД.
    :param user_id: id тест-ученика.
    """
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest_asyncio.fixture
async def course_with_uid(db: AsyncSession) -> AsyncGenerator[dict, None]:
    """Курс с уникальным course_uid. Чистит за собой.

    :param db: асинхронная сессия БД.
    :yield: словарь {"id": int, "course_uid": str}.
    """
    course_uid = f"TSK127-BYCODE-{random.randint(10**6, 10**7)}"
    row = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid) "
            "VALUES (:t, 'self_guided', :uid) RETURNING id"
        ),
        {"t": "tsk127 by-code курс", "uid": course_uid},
    )
    course_id = int(row.scalar_one())
    await db.commit()
    try:
        yield {"id": course_id, "course_uid": course_uid}
    finally:
        await db.execute(text("DELETE FROM courses WHERE id=:c"), {"c": course_id})
        await db.commit()


@pytest.mark.asyncio
async def test_by_code_student_cookie_returns_200(db, client, course_with_uid):
    """Студенческая cookie-сессия → 200 и корректное тело CourseRead."""
    user_id, token = await _setup_student_session(db)
    client.cookies.set("session", token)
    try:
        resp = await client.get(
            f"/api/v1/courses/by-code/{course_with_uid['course_uid']}"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == course_with_uid["id"]
        assert body["course_uid"] == course_with_uid["course_uid"]
    finally:
        await _cleanup_user(db, user_id)


@pytest.mark.asyncio
async def test_by_code_anonymous_returns_401(client, course_with_uid):
    """Анонимный запрос (без cookie, без api_key) → 401."""
    resp = await client.get(
        f"/api/v1/courses/by-code/{course_with_uid['course_uid']}"
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_by_code_service_key_returns_200(client, course_with_uid):
    """Сервисный ключ → 200: обратная совместимость для service-вызовов."""
    resp = await client.get(
        f"/api/v1/courses/by-code/{course_with_uid['course_uid']}",
        params={"api_key": _API_KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == course_with_uid["id"]
    assert body["course_uid"] == course_with_uid["course_uid"]


@pytest.mark.asyncio
async def test_by_code_unknown_uid_returns_404(db, client):
    """Несуществующий course_uid под валидной сессией → 404."""
    user_id, token = await _setup_student_session(db)
    client.cookies.set("session", token)
    try:
        resp = await client.get(
            "/api/v1/courses/by-code/TSK127-NO-SUCH-UID"
        )
        assert resp.status_code == 404, resp.text
    finally:
        await _cleanup_user(db, user_id)
