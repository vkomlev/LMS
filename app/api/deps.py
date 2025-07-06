from fastapi import Depends, Security, HTTPException
from fastapi.security.api_key import APIKeyQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_async_db

settings = Settings()
api_key_query = APIKeyQuery(name="api_key", auto_error=False)

async def get_api_key(
    key: str | None = Security(api_key_query),
) -> str:
    """
    Проверка api_key в query-параметрах.
    """
    if not key or key not in settings.valid_api_keys:
        raise HTTPException(403, "Invalid or missing API Key")
    return key

async def get_db(
    db: AsyncSession = Depends(get_async_db),
    api_key: str = Depends(get_api_key),
) -> AsyncSession:
    """
    Пробросим сразу и сессию, и проверенный API-ключ.
    """
    return db
