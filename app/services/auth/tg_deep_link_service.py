"""Привязка Telegram к кабинету ученика по глубокой ссылке на бота (tsk-1253).

**Зачем.** Бот ученика находит человека по `users.tg_id`, а заполнен он у 4
пользователей из 182 (замер 20.10): привязку из кабинета убрали вместе с
TG-входом (tsk-329), а прежний путь (`/me/identity/tg/link`) требует initData и
работает только внутри мини-приложения. Без привязки напоминания и сообщения в
Telegram не доходят.

**Как.** Кабинет просит ссылку → LMS выпускает одноразовый `link_token`
(тот же сервис, что у привязки почты и ВК, 5 минут, хранится только хеш) и
отдаёт `t.me/<бот>?start=tg_<токен>`. Ученик жмёт «Запустить» в боте → бот
шлёт токен и свой `tg_id` сервисным ключом → LMS гасит токен и пишет
identity `tg` (вместе с `users.tg_id`, ADR-0021 §3).

Имя бота берётся из `QUIZ_FUNNEL_BOT_USERNAME`: воронка квиза живёт в том же
ученическом боте (tsk-1139). Пусто (экземпляр без бота) — кнопки нет.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.audit_service import log_event
from app.services.auth import identity_link_service, link_token_service
from app.services.rate_limit_service import get_redis

logger = logging.getLogger(__name__)

#: Префикс параметра /start. Короткий: у Telegram предел 64 символа, токен ~43.
START_PREFIX = "tg_"


class BotNotConfiguredError(RuntimeError):
    """У экземпляра нет ученического бота."""


def bot_username(settings: Optional[Settings] = None) -> str:
    """Имя ученического бота без «@» или пустая строка."""
    return (settings or Settings()).quiz_funnel_bot_username.strip().lstrip("@")


async def issue_deep_link(user_id: int, settings: Optional[Settings] = None) -> tuple[str, datetime]:
    """Выпустить ссылку на бота с одноразовым токеном.

    :raises BotNotConfiguredError: бот не настроен.
    :raises link_token_service.LinkTokenServiceUnavailableError: хранилище недоступно.
    """
    settings = settings or Settings()
    name = bot_username(settings)
    if not name:
        raise BotNotConfiguredError("ученический бот не настроен")
    raw, expires_at = await link_token_service.issue(get_redis(settings.redis_url), user_id, "tg")
    return f"https://t.me/{name}?start={START_PREFIX}{raw}", expires_at


async def link_from_bot(db: AsyncSession, *, raw_token: str, tg_id: int) -> int:
    """Погасить токен из /start и привязать Telegram к его владельцу.

    :returns: ID ученика.
    :raises link_token_service.LinkTokenError: токен неверный, истёк или не для TG.
    :raises IdentityConflictError: этот Telegram уже привязан к другому человеку.
    """
    settings = Settings()
    payload = await link_token_service.consume(get_redis(settings.redis_url), raw_token)
    if payload.kind != "tg":
        raise link_token_service.LinkTokenError("invalid")
    await identity_link_service.link_existing_user(db, payload.user_id, "tg", str(tg_id))
    await log_event(
        db, "auth.identity.linked", user_id=payload.user_id,
        details={"kind": "tg", "source": "bot_deep_link"},
    )
    await db.commit()
    logger.info("tsk-1253: Telegram привязан к ученику %s через бота", payload.user_id)
    return payload.user_id
