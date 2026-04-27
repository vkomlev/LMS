"""Dependency для service-level API key (legacy ?api_key= query param и X-API-Key header)."""
from __future__ import annotations

from app.core.config import Settings

_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def is_valid_service_key(key: str | None) -> bool:
    """Проверить, что key входит в список VALID_API_KEYS."""
    if not key:
        return False
    return key in _get_settings().valid_api_keys
