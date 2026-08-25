"""Типы и таксономия ошибок общего LLM-клиента (tsk-572 этап 1).

Контракт: docs/specs/2026-08-06-contract-llm-client.md, §4-§6.
Модуль принадлежит tsk-572; tsk-302 потребляет, не правит.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    """Одно сообщение диалога в формате, общем для OpenAI-совместимых провайдеров."""

    role: Role
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResult:
    """Итог батч-вызова. Разбор предметного содержимого — на стороне потребителя."""

    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    attempts: int = 1


@dataclass
class LLMChunk:
    """Кусок потока.

    `done=True` приходит ровно один раз и несёт учётные поля. `truncated=True`
    означает обрыв ПОСЛЕ первого куска: потребитель показывает ученику незавершённый
    ответ и предложение продолжить, а не ошибку (контракт §4.2).
    """

    delta: str = ""
    done: bool = False
    truncated: bool = False
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    attempts: int = 1


class Budget(Enum):
    """Профиль таймаутов. Параметр вызова, не глобальная настройка: один клиент
    обслуживает интерактив и фон одновременно (контракт §6)."""

    INTERACTIVE = "interactive"
    BATCH = "batch"

    @property
    def connect_timeout(self) -> float:
        return 2.0 if self is Budget.INTERACTIVE else 5.0

    @property
    def first_token_timeout(self) -> Optional[float]:
        """Бюджет до первого куска. У батча его нет — там важен только общий.

        12 c, а не 5 c, как было записано в контракте до замеров. Стенд
        2026-08-06 дал медиану первого токена 4.4-4.6 c у годных моделей —
        бюджет, равный медиане, по определению обрывает примерно половину
        ответов. Живой прогон регресс-сценариев это подтвердил: наставник
        отвалился по таймауту на третьем ходе разговора, который шёл нормально.
        Порог должен стоять там, где ответ действительно не придёт, а не там,
        где модель просто думает чуть дольше обычного.
        """
        return 12.0 if self is Budget.INTERACTIVE else None

    @property
    def total_timeout(self) -> float:
        """Общий бюджет. У интерактива 40 c: стриминг показывает текст по мере
        генерации, поэтому ученик не ждёт вслепую — ограничение нужно лишь как
        защита от зависшего соединения, а не как мера терпения."""
        return 40.0 if self is Budget.INTERACTIVE else 60.0

    @property
    def timeout_retries(self) -> int:
        """Ученик не ждёт второй круг — интерактив таймаут не повторяет."""
        return 0 if self is Budget.INTERACTIVE else 1


# ─────────────────────────── Таксономия ошибок ──────────────────────────────
#
# `retryable` — по просьбе чипа tsk-302 (§12.1, дополнение 2): фоновая очередь
# должна отличать «повторить в следующий заход» от «повторять бесполезно», не
# выводя это заново из класса в каждом потребителе.
#
# `try_next_model` — переход по цепочке моделей (§6a). Отделён от `retryable`
# намеренно: 429 повторять нельзя И менять модель бессмысленно (остывает
# провайдер целиком), а «модель не найдена» — наоборот, повторять той же моделью
# бесполезно, но следующая в цепочке может ответить.


class LLMError(Exception):
    """Базовая ошибка транспорта."""

    retryable: bool = False
    try_next_model: bool = False
    alert_staff: bool = False


class LLMRateLimited(LLMError):
    """HTTP 429. Повтор ЗАПРЕЩЁН — кормит брейкер провайдера (инцидент 2026-07-05)."""

    retryable = True          # но не сейчас: сначала провайдер должен остыть
    try_next_model = False


class LLMTimeout(LLMError):
    """Превышен бюджет времени.

    `try_next_model` — молчащая модель это свойство МОДЕЛИ, а не провайдера:
    соседняя по цепочке отвечает (tsk-671). Бесконечного ожидания это не создаёт
    — перебор ограничен общим потолком вызова в `stream`/`complete`.
    """

    retryable = True
    try_next_model = True


class LLMUnavailable(LLMError):
    """Сеть, DNS, TLS, HTTP 5xx на уровне транспорта."""

    retryable = True


class LLMUpstreamUnavailable(LLMUnavailable):
    """HTTP 5xx ОТ ПРОВАЙДЕРА про конкретную модель — берём следующую (tsk-666).

    Отделено от сетевого `LLMUnavailable` намеренно. Сеть, DNS и TLS отвалились —
    менять модель бессмысленно, адрес тот же, а четыре таймаута подряд стоят
    ученику вчетверо большего ожидания. А вот ответ провайдера
    `503 no_available_provider … "requested_models": ["x-ai/grok-4.1-fast"]`
    говорит про ОДНУ модель, и остальные три в цепочке при этом живы.

    Боевая цена прежнего поведения: 503 на первой модели клал наставника целиком.
    Так оборвались оба последних живых разговора контура (22.08 и 24.08) и
    разговор Шестаева 12.08 — ученик получал «сбой на нашей стороне» при трёх
    работающих запасных.

    То же правило уже действует для `LLMConfigError` («модель не найдена»): та же
    логика — повторять этой моделью бесполезно, следующая может ответить.
    """

    retryable = True
    try_next_model = True


class LLMConfigError(LLMError):
    """401/403 или несуществующая модель — ошибка настройки, не ученика."""

    retryable = False
    try_next_model = True
    alert_staff = True


class LLMMalformed(LLMError):
    """Ответ без `choices`, битый SSE — повторять бесполезно."""

    retryable = False


class LLMCooldown(LLMError):
    """Провайдер остывает после 429: отказ сразу, без сетевого вызова."""

    retryable = True


class LLMUpstreamError(LLMError):
    """`{"error": ...}` ВНУТРИ потока при HTTP 200.

    Провайдер отдаёт статус 200 и `text/event-stream`, а внутри — ошибку upstream.
    Наивный клиент увидит «успех, ноль контента» и покажет ученику пустой ответ
    как нормальный. Проверено живьём 2026-08-06.
    """

    retryable = False
    try_next_model = True


class LLMQuotaExceeded(LLMUpstreamError):
    """`pre-consume quota failed` — кончилась квота КЛЮЧА (не баланс аккаунта)."""

    retryable = False
    try_next_model = True
    alert_staff = True


@dataclass
class UsageRecord:
    """Строка учёта расхода (контракт §8). Пишется вне транзакции потребителя."""

    purpose: str
    model: str
    provider: str
    outcome: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    student_id: Optional[int] = None
    meta: dict = field(default_factory=dict)
