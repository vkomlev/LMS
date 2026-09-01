"""Схемы раздела «попытки входа, о которых стоит знать» (tsk-755)."""

from datetime import datetime

from pydantic import BaseModel, Field


class UnknownRecipientAttempt(BaseModel):
    """Одна попытка входа на адрес, которого нет ни у кого.

    Строки по одному адресу схлопнуты: `attempts` — сколько раз заказывали
    ссылку, `first_attempt_at` — когда человек начал биться в закрытую дверь.
    """

    email: str = Field(description="Адрес целиком — по нему и видно опечатку")
    attempts: int = Field(description="Сколько раз заказывали ссылку на этот адрес")
    first_attempt_at: datetime = Field(description="Первая попытка в окне")
    last_attempt_at: datetime = Field(description="Последняя попытка")
    last_ip: str | None = Field(default=None, description="Адрес сети последней попытки")


class UnknownRecipientAttemptsResponse(BaseModel):
    """Список попыток за окно."""

    window_days: int = Field(description="Глубина окна в днях")
    total: int = Field(description="Сколько разных адресов нашлось в окне")
    items: list[UnknownRecipientAttempt]
