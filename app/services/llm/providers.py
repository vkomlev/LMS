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
# Цепочки пересобраны 2026-08-25 (tsk-671) по ЖИВОМУ замеру всех 75 моделей
# маршрутизатора с прод-хоста: из них отвечали ~45, а прежние цепочки целиком
# состояли из недоступных и медленных. Порядок — по времени первого токена на
# НАСТОЯЩЕМ промпте наставника (5178 символов), голова — решение оператора.
#
# Кого сознательно НЕ берём в наставники:
#   deepseek/*            — сливал эталон 1 раз на ~9 прогонов (стенд 2026-08-06);
#   minimax/*             — льёт `<think>`-рассуждения прямо в поток, ученик увидит;
#   qwen/qwen3.5-flash    — молчал дольше 45 c;
#   openai/gpt-5.4        — 17,8 c до первого токена, за нашим пределом 12 c.
#
# `openai/gpt-5.5` оставлен, но НЕ первым: 11,3 c при пределе 12 c — он на грани,
# и часть обращений срезалась таймаутом (наблюдалось на бою).
_DEFAULT_TUTOR_MODELS = (
    "anthropic/claude-sonnet-5,"      # 2,4 c — голова, решение оператора
    "anthropic/claude-haiku-4.5,"     # 1,8 c
    "google/gemini-3.6-flash,"        # 2,7 c
    "anthropic/claude-sonnet-4.6,"    # 4,0 c
    "x-ai/grok-4.5,"                  # 7,0 c — замена умершему grok-4.1-fast
    "openai/gpt-5.5"                  # 11,3 c — последний рубеж
)
# deepseek стоит в судьях, но НЕ в наставниках: он сливал эталон 1 раз на ~9
# прогонов. Судья с учеником не разговаривает, там слив не критерий; в чате
# это означало бы, что каждый девятый получает ответ в руки.
# У судьи первая модель прежняя (её выбирал стенд по дисциплине формата), но все
# три запасные были мертвы — отсюда вызовы `code_review` по 63–95 c. Заменены на
# живые. deepseek здесь допустим: судья с учеником не разговаривает.
_DEFAULT_JUDGE_MODELS = (
    "openai/gpt-5.4-mini,anthropic/claude-haiku-4.5,"
    "google/gemini-3.6-flash,deepseek/deepseek-v4-flash-0731"
)


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


def model_cooldown_seconds() -> float:
    """Сколько модель считается «только что отказавшей» и уходит в конец очереди.

    Ротация (tsk-671). Смысл не в наказании модели, а в том, чтобы СЛЕДУЮЩИЙ
    ученик не платил за уже известный отказ: первый упёрся в мёртвую или
    молчащую модель, остальные её какое-то время обходят. Две минуты — компромисс:
    достаточно, чтобы переждать типичную аварию маршрута, и мало, чтобы вернуться
    к лучшей модели, когда она оживёт.

    Состав цепочки ротация НЕ меняет: он утверждён стендом по гейту на слив
    эталона, и подставлять туда «живую» модель мимо стенда нельзя (25.08:
    claude-opus-4.8 и claude-haiku-4.5 отвечали быстро и слили ответ 3 раза из 3).
    """
    raw = _env_first("LLM_MODEL_COOLDOWN_SECONDS")
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        logger.warning("LLM: LLM_MODEL_COOLDOWN_SECONDS=%r не число, беру 120", raw)
        return 120.0


def cooldown_seconds() -> float:
    raw = _env_first("LLM_COOLDOWN_SECONDS")
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        logger.warning("LLM: LLM_COOLDOWN_SECONDS=%r не число, беру 120", raw)
        return 120.0
