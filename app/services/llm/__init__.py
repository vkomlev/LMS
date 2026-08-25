"""Общий LLM-клиент LMS (tsk-572 этап 1).

Публичный фасад: `complete` (батч) и `stream` (интерактив). Всё остальное —
внутреннее устройство модуля.

Контракт: docs/specs/2026-08-06-contract-llm-client.md.
Владелец модуля — tsk-572. tsk-302 потребляет, не правит.

Что клиент НЕ делает (границы, §9): не парсит предметные ответы, не знает про
задания и педагогику, не хранит историю диалога, не режет по лимитам.
"""
from app.services.llm.client import complete, stream
from app.services.llm.contracts import (
    Budget,
    LLMChunk,
    LLMConfigError,
    LLMCooldown,
    LLMError,
    LLMMalformed,
    LLMMessage,
    LLMQuotaExceeded,
    LLMRateLimited,
    LLMResult,
    LLMTimeout,
    LLMUnavailable,
    LLMUpstreamUnavailable,
    LLMUpstreamError,
    UsageRecord,
)

__all__ = [
    "complete",
    "stream",
    "Budget",
    "LLMMessage",
    "LLMResult",
    "LLMChunk",
    "UsageRecord",
    "LLMError",
    "LLMRateLimited",
    "LLMTimeout",
    "LLMUnavailable",
    "LLMUpstreamUnavailable",
    "LLMConfigError",
    "LLMMalformed",
    "LLMCooldown",
    "LLMUpstreamError",
    "LLMQuotaExceeded",
]
