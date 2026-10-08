"""Срок ручной проверки работы (tsk-1176).

Одно место, где из момента сдачи выводятся срок, возраст и просрочка. Им
пользуются очередь преподавателя (`list_pending_reviews`), сводка внимания
бота (`teacher_attention_service`), ответ сдачи и история ученика — чтобы
ученик и преподаватель видели один и тот же срок, а не две разные арифметики.

Срок не хранится в БД: он выводится из `submitted_at` и настройки
`REVIEW_SLA_HOURS`. Смена настройки сразу меняет срок у всех ожидающих работ.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _aware(moment: Optional[datetime], *, what: str) -> Optional[datetime]:
    """Привести момент к aware-UTC; `None` и не-datetime → `None`.

    Колонки `timestamptz` приходят aware. Naive datetime — признак ошибки
    вызывающего: трактуем как UTC (так пишет весь проект) и пишем WARNING,
    а не роняем ответ ученику из-за подписи под результатом.
    """
    if moment is None:
        return None
    if not isinstance(moment, datetime):
        logger.warning("review_sla: %s не datetime (%r) — срок не считаем", what, type(moment))
        return None
    if moment.tzinfo is None:
        logger.warning("review_sla: naive %s=%s — трактуем как UTC", what, moment)
        return moment.replace(tzinfo=timezone.utc)
    return moment


def sla_hours() -> int:
    """Срок проверки в часах из настроек."""
    return int(Settings().review_sla_hours)


def reminder_hours() -> int:
    """Возраст работы (ч), с которого бот напоминает преподавателю."""
    return int(Settings().review_reminder_hours)


def review_due_at(submitted_at: Optional[datetime]) -> Optional[datetime]:
    """Срок проверки: момент сдачи + `REVIEW_SLA_HOURS`."""
    sub = _aware(submitted_at, what="submitted_at")
    return None if sub is None else sub + timedelta(hours=sla_hours())


def review_age_hours(submitted_at: Optional[datetime], now: Optional[datetime] = None) -> Optional[float]:
    """Сколько часов работа ждёт проверки (не меньше 0, округлено до 0,1)."""
    sub = _aware(submitted_at, what="submitted_at")
    if sub is None:
        return None
    current = _aware(now, what="now") or datetime.now(timezone.utc)
    return round(max((current - sub).total_seconds(), 0.0) / 3600, 1)


def is_review_overdue(submitted_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """Просрочена ли проверка: срок уже прошёл."""
    due = review_due_at(submitted_at)
    if due is None:
        return False
    current = _aware(now, what="now") or datetime.now(timezone.utc)
    return due < current
