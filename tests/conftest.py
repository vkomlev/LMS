"""Общие фикстуры для тестов Phase Y-1+."""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

# Windows: asyncpg несовместим с ProactorEventLoop по умолчанию
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import Settings
from app.api.main import app
from app.db.session import get_async_db

_settings = Settings()
_logger = logging.getLogger(__name__)

# id «реальных» пользователей dev-БД, которых тестовый sweep не трогает
# (см. scripts/cleanup_test_artifacts.py)
_REAL_USER_IDS: tuple[int, ...] = (2, 3, 142)


async def _snapshot_ts() -> datetime:
    """Зафиксировать серверный timestamp PG для последующего sweep'а."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT now()"))
            return row.scalar()
    finally:
        await engine.dispose()


async def _sweep_test_artifacts(snapshot_ts: datetime) -> dict[str, int]:
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    real_csv = ",".join(str(i) for i in _REAL_USER_IDS)
    counts = {"users": 0, "audit_event": 0, "magic_link": 0, "guest_session": 0}
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE notifications SET modified_by = NULL "
                    f"WHERE modified_by IS NOT NULL "
                    f"AND modified_by NOT IN ({real_csv}) "
                    f"AND modified_by IN ("
                    f"  SELECT id FROM users "
                    f"  WHERE created_at >= :ts "
                    f"  AND (email IS NULL OR email ILIKE '%@example.%')"
                    f")"
                ),
                {"ts": snapshot_ts},
            )
            await conn.execute(
                text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_modify")
            )
            counts["audit_event"] = (
                await conn.execute(
                    text(
                        f"DELETE FROM audit_event "
                        f"WHERE ts >= :ts "
                        f"AND (user_id IS NULL OR user_id NOT IN ({real_csv}))"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["magic_link"] = (
                await conn.execute(
                    text(
                        "DELETE FROM magic_link "
                        "WHERE created_at >= :ts "
                        "AND (email IS NULL OR email ILIKE '%@example.%')"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["guest_session"] = (
                await conn.execute(
                    text("DELETE FROM guest_session WHERE created_at >= :ts"),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            counts["users"] = (
                await conn.execute(
                    text(
                        f"DELETE FROM users "
                        f"WHERE created_at >= :ts "
                        f"AND id NOT IN ({real_csv}) "
                        f"AND (email IS NULL OR email ILIKE '%@example.%')"
                    ),
                    {"ts": snapshot_ts},
                )
            ).rowcount
            await conn.execute(
                text("ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_modify")
            )
        return counts
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_artifacts():
    """Session-scoped sweep тестовых артефактов в dev-БД (Learn.public).

    Снимает мусор, который тесты успели закоммитить за прогон pytest:
    users с email `@example.*` или без email, плюс непривязанные к
    «реальным» (id 2,3,142) audit_event / magic_link / guest_session
    созданные за время прогона. CASCADE от `users` подбирает остальное
    (user_roles, identity_link, user_session, attempts, task_results,
    learning_events, notifications, teacher_courses, user_courses,
    student_*, access_requests, social_posts, user_achievements,
    help_requests + replies, messages.recipient).

    Технические нюансы:
    - `audit_event` имеет триггер `audit_event_no_modify` (append-only)
      и FK с `SET NULL` → DELETE на users падает. Триггер временно
      отключается в той же транзакции и возвращается обратно.
    - `notifications.modified_by` FK с `NO ACTION` → обнуляется заранее.
    - Два слоя защиты от случайного удаления real users:
        1. фильтр по email `ILIKE '%@example.%'` (real users имеют
           реальные домены mail.ru/list.ru/gmail.com)
        2. allow-list `_REAL_USER_IDS` (2,3,142)
    - Фикстура синхронная: внутри `asyncio.run` создаёт изолированный
      event loop, поэтому не конфликтует с function-scoped event loop
      теста (pytest-asyncio 1.x).
    - Snapshot — `now()` PG-сервера на старте сессии; sweep удаляет
      строки `created_at >= snapshot`. Это позволяет работать с
      таблицами, где `id` UUID (например, `guest_session`).
    """
    snapshot_ts = asyncio.run(_snapshot_ts())
    _logger.info("test-artifacts sweep snapshot_ts: %s", snapshot_ts)
    yield
    try:
        counts = asyncio.run(_sweep_test_artifacts(snapshot_ts))
        _logger.info(
            "test-artifacts sweep done: users=%d audit_event=%d "
            "magic_link=%d guest_session=%d",
            counts["users"],
            counts["audit_event"],
            counts["magic_link"],
            counts["guest_session"],
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception(
            "test-artifacts sweep failed (manual cleanup may be needed): %s",
            exc,
        )


@pytest_asyncio.fixture(scope="function")
async def db():
    """Асинхронная сессия к БД с NullPool; rollback после каждого теста.

    NullPool гарантирует, что каждый тест получает свежее соединение,
    не связанное с event loop предыдущего теста.
    """
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client():
    """HTTP-клиент для ASGI-тестов.

    Переопределяет get_async_db чтобы использовать NullPool —
    иначе глобальный QueuePool хранит соединения из предыдущих event loop'ов.
    """
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_async_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_async_db, None)
        await engine.dispose()
