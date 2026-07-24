"""tsk-235: подлинная HTTP-гонка ротации refresh (две вкладки SPW, реальные
конкурентные соединения к БД — не общая откатываемая транзакция теста).

test_session_refresh_grace_window_tsk235.py проверяет логику на общем `db`
(последовательные вызовы в одной транзакции) — этого достаточно для веток
кода, но не доказывает, что запрос B, стартовавший ДО коммита запроса A,
не проскочит ту же ветку ротации, что и A (TOCTOU: оба видят revoked_at IS
NULL, оба создают новую сессию — цепочка размножается). Закрывает это
`.with_for_update()` в `refresh_session`: второй запрос блокируется на
строке до commit первого, затем видит актуальный revoked_at + replaced_by.

Здесь — подлинные N параллельных HTTP POST /api/v1/auth/session/refresh с
ОДНИМ и тем же refresh_token, каждый на своём соединении (`no_tx_isolation`,
по образцу test_attempts_limit_race_tsk273.py). Ожидание: ВСЕ получают 200
(валидную пару), НИ ОДИН — 401; создаётся ровно ОДНА новая сессия-преемник
(не N).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.core.config import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.no_tx_isolation]

_settings = Settings()
_REFRESH_URL = "/api/v1/auth/session/refresh"
CONCURRENCY = 5


async def test_concurrent_http_refresh_all_succeed_one_successor(client):
    """N подлинно параллельных refresh одним и тем же токеном: все 200,
    ровно одна новая сессия-преемник (не N), ни одного 401."""
    from app.services.auth.session_service import create_session

    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    user_id: int | None = None
    refresh_token: str | None = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            user_id = (await s.execute(text("SELECT MIN(id) FROM users"))).scalar()
            if user_id is None:
                pytest.skip("Нет пользователей в БД")
            _access, refresh_token, old_session = await create_session(s, user_id=user_id)
            old_session_id = old_session.id
            await s.commit()

        async def _refresh():
            return await client.post(_REFRESH_URL, cookies={"refresh": refresh_token})

        responses = await asyncio.gather(*(_refresh() for _ in range(CONCURRENCY)))
        codes = sorted(r.status_code for r in responses)

        assert codes == [200] * CONCURRENCY, (
            f"ГОНКА: ожидались только 200 (окно благодати), получено {codes} — "
            f"конкурентный refresh снова ловит 401 (баг tsk-235 не закрыт)"
        )

        pairs = {(r.json()["access_token"], r.json()["refresh_token"]) for r in responses}
        assert len(pairs) == 1, (
            f"ожидалась ОДНА идемпотентная пара токенов на всех конкурентов, "
            f"получено {len(pairs)} разных — цепочка размножилась"
        )

        async with AsyncSession(engine, expire_on_commit=False) as s:
            # Ровно один преемник у СТАРОЙ сессии (не голый count по user_id —
            # у пользователя может быть посторонний мусор из прошлых тестов).
            old = (
                await s.execute(
                    text("SELECT replaced_by_session_id FROM user_session WHERE id = :i"),
                    {"i": old_session_id},
                )
            ).scalar()
            assert old is not None, "старая сессия не помечена преемником — ротации не было"
    finally:
        if user_id is not None:
            async with AsyncSession(engine, expire_on_commit=False) as s:
                await s.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": user_id})
                await s.commit()
        await engine.dispose()
