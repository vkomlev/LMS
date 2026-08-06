"""Остывание провайдера после HTTP 429 (tsk-572 этап 1).

Контракт: docs/specs/2026-08-06-contract-llm-client.md, §7.

Состояние держим В ПРОЦЕССЕ, не в БД. Переживать перезапуск не требуется, а
лишняя запись в горячем пути ни к чему. При нескольких воркерах остывание
независимо у каждого — это принято сознательно: цель не идеальная синхронизация,
а «не долбить провайдера всей толпой». Брейкер CloseRouter уже срабатывал от
агрессивного цикла (2026-07-05), и повтор на 429 кормит именно его.
"""
from __future__ import annotations

import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

_until: Dict[str, float] = {}


def start(provider: str, seconds: float) -> None:
    """Пометить провайдера остывающим. Продлевает, но не сокращает срок."""
    until = time.monotonic() + max(0.0, seconds)
    if until > _until.get(provider, 0.0):
        _until[provider] = until
        logger.warning(
            "LLM: провайдер %s уходит в остывание на %.0f c (получен 429)",
            provider, seconds,
        )


def remaining(provider: str) -> float:
    """Сколько секунд осталось остывать. 0 — можно вызывать."""
    until = _until.get(provider)
    if until is None:
        return 0.0
    left = until - time.monotonic()
    if left <= 0:
        _until.pop(provider, None)
        return 0.0
    return left


def is_cooling(provider: str) -> bool:
    return remaining(provider) > 0


def reset(provider: str | None = None) -> None:
    """Сброс. Нужен тестам и ручному вмешательству, в горячем пути не зовётся."""
    if provider is None:
        _until.clear()
    else:
        _until.pop(provider, None)
