from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import Settings

settings = Settings()

# Асинхронный движок
engine = create_async_engine(
    settings.database_url,
    echo=False,  # SQL-вывод можно включить через LOG_LEVEL=DEBUG
    future=True,
)

# Фабрика сессий
async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Зависимость FastAPI: открывает сессию и гарантированно её закрывает.
    """
    async with async_session_factory() as session:
        yield session
