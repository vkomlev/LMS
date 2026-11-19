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


def is_hint_review_key(key: str | None) -> bool:
    """tsk-1547: совпадение с выделенным ключом штатной вычитки черновиков.

    Ключ живёт отдельно от VALID_API_KEYS намеренно: обычные боты
    (TG_LMS, ContentBackbone) вычитку не делают, и права «читать/писать
    контент» у них не должны молча означать право подтверждать подсказки.
    Пустая настройка — capability выключена, функция всегда False.
    """
    if not key:
        return False
    hint_key = _get_settings().hint_review_api_key
    return bool(hint_key) and key == hint_key
