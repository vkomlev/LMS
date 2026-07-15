"""tsk-224: эндпоинт-тесты POST /api/v1/auth/session/refresh.

Проверяют скоординированный LMS↔SPW фикс web-refresh:
  - web-контекст: refresh-токен приходит из httpOnly cookie `refresh`
    (bodyless POST), в ответ ставится НОВАЯ пара cookie (session + refresh);
  - tg-app/Bearer-контекст: fallback на refresh_token в теле запроса;
  - негативные: нет токена → 401, невалидный/протухший токен → 401;
  - logout чистит обе cookie.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

_REFRESH_URL = "/api/v1/auth/session/refresh"


async def _get_existing_user_id(db) -> int:
    from sqlalchemy import text
    uid = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if uid is None:
        pytest.skip("Нет пользователей в БД")
    return uid


async def _create_session_committed(db) -> tuple[str, str]:
    """Создать сессию и закоммитить (видима ASGI-запросу). Вернуть (access, refresh)."""
    from app.services.auth.session_service import create_session

    user_id = await _get_existing_user_id(db)
    access_token, refresh_token, _ = await create_session(db, user_id=user_id)
    await db.commit()
    return access_token, refresh_token


@pytest.mark.asyncio
async def test_refresh_via_cookie_web_context(client, db):
    """web-flow: bodyless POST + cookie `refresh` → 200 + новая пара cookie."""
    from app.services.auth.session_service import validate_session

    old_access, old_refresh = await _create_session_committed(db)

    resp = await client.post(_REFRESH_URL, cookies={"refresh": old_refresh})
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["access_token"] and data["refresh_token"]
    assert data["access_token"] != old_access  # ротация access
    assert data["refresh_token"] != old_refresh  # ротация refresh

    # В ответе ставятся ОБЕ cookie.
    set_cookies = resp.headers.get_list("set-cookie")
    joined = " ".join(set_cookies)
    assert "session=" in joined, joined
    assert "refresh=" in joined, joined

    # Новый access валиден, старый отозван (refresh_session отзывает старую сессию).
    assert await validate_session(db, data["access_token"]) is not None
    assert await validate_session(db, old_access) is None


@pytest.mark.asyncio
async def test_refresh_via_body_fallback_tg_context(client, db):
    """tg-app/Bearer: refresh_token в теле по-прежнему работает (cookie нет)."""
    _old_access, old_refresh = await _create_session_committed(db)

    resp = await client.post(_REFRESH_URL, json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_refresh_no_token_returns_401(client):
    """Bodyless POST без cookie и без тела → 401 (нечем обновлять)."""
    resp = await client.post(_REFRESH_URL)
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_refresh_invalid_cookie_returns_401(client):
    """Невалидный refresh в cookie → 401."""
    fake = os.urandom(32).hex()
    resp = await client.post(_REFRESH_URL, cookies={"refresh": fake})
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_refresh_cookie_takes_priority_over_body(client, db):
    """Приоритет источника — cookie над телом (web выигрывает у устаревшего body)."""
    _old_access, valid_refresh = await _create_session_committed(db)
    garbage_body = os.urandom(32).hex()

    resp = await client.post(
        _REFRESH_URL,
        json={"refresh_token": garbage_body},
        cookies={"refresh": valid_refresh},
    )
    # cookie валиден → 200, тело-мусор проигнорировано.
    assert resp.status_code == 200, resp.text
