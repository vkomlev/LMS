"""tsk-604: соразмерность отзыва при повторе refresh-токена + причина отказа в логе.

До tsk-604 повтор refresh-токена позже окна благодати (tsk-235) вызывал
`revoke_all_sessions` — ученика выкидывало со ВСЕХ устройств сразу. По фактам
прода за две недели защита сработала дважды, и оба раза не на воре, а на живом
ученике: браузер не сохранил новую пару cookie (кросс-домен, закрыто в tsk-603).

Теперь гасится только та цепочка сессий, которой принадлежит токен. Угон это
по-прежнему обрубает — вор и владелец сидят в одной цепочке. Покрывается:

- Повтор гасит свою цепочку целиком (включая преемника преемника), но сессия
  другого устройства того же ученика остаётся живой.
- Причина отказа продления пишется в лог одной строкой для каждого класса:
  no_token / malformed / unknown / expired / revoked / replay.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

_SERVICE_LOGGER = "app.services.auth.session_service"


async def _get_existing_user_id(db) -> int:
    uid = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if uid is None:
        pytest.skip("Нет пользователей в БД")
    return uid


@pytest_asyncio.fixture()
async def user_id(db) -> int:
    return await _get_existing_user_id(db)


def _denied_reasons(caplog) -> list[str]:
    """Коды причин отказа из строк лога сервиса сессий."""
    return [
        record.getMessage().split("reason=")[1].split(" ")[0]
        for record in caplog.records
        if record.name == _SERVICE_LOGGER and "auth.refresh denied" in record.getMessage()
    ]


async def test_replay_revokes_only_own_chain(db, user_id):
    """Мирный повтор на одном устройстве не отбирает доступ на другом.

    Ученик вошёл с телефона и с ноутбука — это две независимые цепочки. Повтор
    токена ноутбука гасит цепочку ноутбука, телефон продолжает работать.
    """
    from app.services.auth.session_service import (
        _REFRESH_GRACE_WINDOW_SECONDS,
        _now,
        create_session,
        refresh_session,
        validate_session,
    )

    phone_access, _phone_refresh, _phone_session = await create_session(db, user_id=user_id)
    laptop_access, laptop_refresh, laptop_session = await create_session(db, user_id=user_id)
    await db.commit()

    rotated = await refresh_session(db, laptop_refresh, None)
    assert rotated is not None
    successor_access, _successor_refresh, _successor_session = rotated

    # Окно благодати истекло (без реального ожидания).
    laptop_session.revoked_at = _now() - timedelta(seconds=_REFRESH_GRACE_WINDOW_SECONDS + 1)
    await db.flush()

    assert await refresh_session(db, laptop_refresh, None) is None

    # Цепочка ноутбука мертва...
    assert await validate_session(db, laptop_access) is None
    assert await validate_session(db, successor_access) is None
    # ...а телефон не тронут: до tsk-604 здесь был None (revoke_all_sessions).
    assert await validate_session(db, phone_access) is not None, (
        "повтор токена на одном устройстве не должен отбирать доступ на других"
    )


async def test_replay_revokes_chain_deeper_than_one_hop(db, user_id):
    """Гасится вся цепочка вперёд, а не только ближайший преемник."""
    from app.services.auth.session_service import (
        _REFRESH_GRACE_WINDOW_SECONDS,
        _now,
        create_session,
        refresh_session,
        validate_session,
    )

    _access, refresh_1, session_1 = await create_session(db, user_id=user_id)
    await db.commit()

    first = await refresh_session(db, refresh_1, None)
    assert first is not None
    access_2, refresh_2, _session_2 = first

    second = await refresh_session(db, refresh_2, None)
    assert second is not None
    access_3, _refresh_3, _session_3 = second

    session_1.revoked_at = _now() - timedelta(seconds=_REFRESH_GRACE_WINDOW_SECONDS + 1)
    await db.flush()

    # Повтор самого первого токена — цепочка уже длиной в три звена.
    assert await refresh_session(db, refresh_1, None) is None

    assert await validate_session(db, access_2) is None
    assert await validate_session(db, access_3) is None, (
        "дальний преемник в цепочке остался живым — обход цепочки не доходит до конца"
    )


async def test_replay_logs_reason_and_scope(db, user_id, caplog):
    """Повтор пишет в лог одну строку с причиной и числом погашенных сессий."""
    from app.services.auth.session_service import (
        _REFRESH_GRACE_WINDOW_SECONDS,
        _now,
        create_session,
        refresh_session,
    )

    _access, refresh_token, session_obj = await create_session(db, user_id=user_id)
    await db.commit()

    assert await refresh_session(db, refresh_token, None) is not None
    session_obj.revoked_at = _now() - timedelta(seconds=_REFRESH_GRACE_WINDOW_SECONDS + 1)
    await db.flush()

    with caplog.at_level(logging.INFO, logger=_SERVICE_LOGGER):
        assert await refresh_session(db, refresh_token, None) is None

    replay_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == _SERVICE_LOGGER and "reason=replay" in record.getMessage()
    ]
    assert len(replay_lines) == 1, f"ожидалась ровно одна строка о повторе, получено {replay_lines}"
    line = replay_lines[0]
    assert f"user_id={user_id}" in line
    assert "revoked_sessions=1" in line, f"не записано число погашенных сессий: {line}"
    assert refresh_token not in line, "сам токен не должен попадать в лог"


async def test_denied_reason_logged_for_malformed_and_unknown(db, caplog):
    """Кривой и неизвестный токен различимы в логе, а не сливаются в один 401."""
    from app.services.auth.session_service import refresh_session

    with caplog.at_level(logging.INFO, logger=_SERVICE_LOGGER):
        assert await refresh_session(db, "не-шестнадцатеричная-строка", None) is None
        assert await refresh_session(db, "ab" * 32, None) is None

    assert _denied_reasons(caplog) == ["malformed", "unknown"]


async def test_denied_reason_logged_for_expired_and_revoked(db, user_id, caplog):
    """Истёкший срок продления и отзыв логаутом — разные причины в логе."""
    from app.services.auth.session_service import (
        _now,
        create_session,
        refresh_session,
        revoke_session,
    )

    _a1, refresh_expired, session_expired = await create_session(db, user_id=user_id)
    _a2, refresh_revoked, session_revoked = await create_session(db, user_id=user_id)
    await db.commit()

    session_expired.refresh_expires_at = _now() - timedelta(seconds=1)
    await db.flush()
    await revoke_session(db, session_revoked.id)

    with caplog.at_level(logging.INFO, logger=_SERVICE_LOGGER):
        assert await refresh_session(db, refresh_expired, None) is None
        assert await refresh_session(db, refresh_revoked, None) is None

    assert _denied_reasons(caplog) == ["expired", "revoked"]


async def test_missing_token_logged_by_router(client, caplog):
    """Запрос без refresh-cookie отдаёт 401 и оставляет причину в логе.

    В tsk-594 этот случай отличали от «токен недействителен» по размеру тела
    ответа в логе nginx (49 байт против 73) — костыль, который тут и убирается.
    """
    router_logger = "app.api.v1.auth.session"
    with caplog.at_level(logging.INFO):
        response = await client.post("/api/v1/auth/session/refresh")

    assert response.status_code == 401
    reasons = [
        record.getMessage()
        for record in caplog.records
        if record.name in (_SERVICE_LOGGER, router_logger)
        and "auth.refresh denied" in record.getMessage()
    ]
    assert any("reason=no_token" in line for line in reasons), (
        f"причина отказа не записана в лог: {reasons}"
    )
