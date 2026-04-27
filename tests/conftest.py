"""Общие фикстуры для тестов Phase Y-1."""
import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest_asyncio
from dotenv import load_dotenv
from httpx import AsyncClient, ASGITransport
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
