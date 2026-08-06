"""Резолв подключения к провайдеру и цепочек моделей (tsk-572 этап 1).

Канон имён переменных — ADR-0046 «connections shared, policy local»:
подключение общее с ContentBackbone, политика (какие модели) локальная.

Контракт: docs/specs/2026-08-06-contract-llm-client.md, §3 и §6a.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.closerouter.dev"
PROVIDER_NAME = "closerouter"

# Цепочки по умолчанию — по стенду 2026-08-06 (docs/qa/2026-08-06-llm-model-bakeoff.md),
# а не по вендору: цена на этой задаче качества не предсказывает.
_DEFAULT_TUTOR_MODELS = "x-ai/grok-4.1-fast,openai/gpt-5.5,openai/gpt-5.4-mini"
_DEFAULT_JUDGE_MODELS = "openai/gpt-5.4-mini,x-ai/grok-4.1-fast,google/gemini-3.1-flash-lite"


def normalize_base_url(raw: str) -> str:
    """Привести базовый адрес к виду без хвостового `/v1`.

    Клиент склеивает путь как `{base}/v1/chat/completions`. Оператор естественным
    образом пишет в `.env` адрес `https://api.closerouter.dev/v1` — и получается
    `/v1/v1/chat/completions`, то есть 404 на ровном месте. Гоча найдена чипом
    tsk-302 живьём, поэтому нормализация здесь, а не в инструкции оператору.
    """
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base or DEFAULT_BASE_URL


@dataclass(frozen=True)
class ProviderConfig:
    """Готовое подключение. `usable=False` — ключа нет, вызывать нельзя."""

    base_url: str
    api_key: Optional[str]
    name: str = PROVIDER_NAME

    @property
    def usable(self) -> bool:
        return bool(self.api_key)

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"


def _env_first(*names: str) -> Optional[str]:
    """Первое непустое значение из переменных по приоритету."""
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def resolve_provider() -> ProviderConfig:
    """Собрать подключение из окружения.

    Приоритет `CLOSEROUTER_*` над legacy-алиасами `CB_CLAUDE_*` (ADR-0046):
    ключ исторически жил в ContentBackbone под именем `CB_CLAUDE_API_KEY`, и
    старое имя оставлено рабочим, чтобы перенос не требовал одновременной правки
    двух проектов.
    """
    base_raw = _env_first("CLOSEROUTER_BASE_URL", "CB_CLAUDE_BASE_URL") or DEFAULT_BASE_URL
    key = _env_first("CLOSEROUTER_API_KEY", "CB_CLAUDE_API_KEY")
    if not key:
        logger.warning(
            "LLM: ключ провайдера не задан (CLOSEROUTER_API_KEY / CB_CLAUDE_API_KEY) — "
            "вызовы будут отклоняться настройкой, а не сетью"
        )
    return ProviderConfig(base_url=normalize_base_url(base_raw), api_key=key)


def _chain(env_name: str, default: str) -> list[str]:
    raw = _env_first(env_name) or default
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models


def tutor_models() -> list[str]:
    """Цепочка для интерактива: обязателен стриминг."""
    return _chain("LLM_TUTOR_MODELS", _DEFAULT_TUTOR_MODELS)


def judge_models() -> list[str]:
    """Цепочка для батча: латентность не важна, важна дисциплина формата."""
    return _chain("LLM_JUDGE_MODELS", _DEFAULT_JUDGE_MODELS)


def cooldown_seconds() -> float:
    raw = _env_first("LLM_COOLDOWN_SECONDS")
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        logger.warning("LLM: LLM_COOLDOWN_SECONDS=%r не число, беру 120", raw)
        return 120.0
