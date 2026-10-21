"""Каналы уведомлений ВКонтакте и MAX (tsk-1253, этап Б).

**Зачем.** У учеников разный набор мессенджеров (решение оператора 21.10):
напоминание и сообщение преподавателя должны доходить туда, где человек
бывает, а не только в кабинет и Telegram.

**Привязка** — как у Telegram в этапе А: кабинет выпускает одноразовый
`link_token` (5 минут, хранится только хеш) и отдаёт ссылку:
- ВК: `https://vk.me/club<id>?ref=<токен>` — ученик пишет сообществу, Callback
  API присылает `message_new` с `ref`;
- MAX: `https://max.ru/<бот>?start=<токен>` — событие `bot_started`, поле
  `payload` (до 128 знаков, документация dev.max.ru).
LMS гасит токен и пишет строку `messenger_link`. Нажал «Подключить» и сам
написал — это и есть волеизъявление ученика (сверка /lawyer 21.10).

**Отправка.** Короткий текст со ссылкой в кабинет, без самого сообщения и
оценок. Только сервисное — рекламу в эти каналы не слать: на неё нужно
отдельное согласие (ст. 18 38-ФЗ). Каждый канал — свой рубильник в кабинете
администратора, выключен по умолчанию.

**Отписка** исполняется сразу: «Отключить» в кабинете, слово «стоп» в
переписке, запрет сообщений в ВК (`message_deny`), остановка бота MAX
(`bot_stopped`), отказ ВК «нельзя писать этому человеку».

Отправка никогда не бросает исключение: это дубль, его сбой не должен ронять
отправку сообщения или проход напоминаний. Токены — только в `.env`, в лог
не попадают.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import ssl
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import certifi
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.config import Settings
from app.services.audit_service import log_event
from app.services.auth import link_token_service
from app.services.rate_limit_service import get_redis

logger = logging.getLogger(__name__)

Channel = Literal["vk", "max"]
CHANNELS: tuple[Channel, ...] = ("vk", "max")

#: Вид link_token для канала: `vk` занят привязкой входа через VK ID.
TOKEN_KIND = {"vk": "vk_msg", "max": "max"}
SETTING_KEY = {"vk": "notify_vk_enabled", "max": "notify_max_enabled"}

VK_API = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.199"
MAX_API = "https://platform-api2.max.ru"
#: Коды ВК «писать этому человеку нельзя»: запретил сообщения / не разрешал.
VK_NO_PERMISSION_CODES = {7, 900, 901, 902}
#: Слова отписки в переписке (сравнение без регистра и знаков).
STOP_WORDS = {"стоп", "stop", "/stop", "отписаться", "отключить", "отключить уведомления"}

KIND_REENGAGE = "reengage"
KIND_MESSAGE = "message"
_TEXTS = {
    KIND_REENGAGE: "Вы давно не заглядывали в курс. Следующее задание уже ждёт: {url}",
    KIND_MESSAGE: "Преподаватель написал вам сообщение. Прочитать: {url}",
}
_FOOTER = "\n\nОтключить уведомления — напишите «стоп»."
LINKED_TEXT = (
    "Готово: уведомления школы (напоминания о заданиях и сообщения "
    "преподавателя) будут приходить сюда." + _FOOTER
)
STOPPED_TEXT = "Уведомления отключены. Снова подключить можно в кабинете ученика."
EXPIRED_TEXT = "Ссылка устарела. Нажмите «Подключить» в кабинете ученика ещё раз."
CONFLICT_TEXT = "Этот аккаунт уже подключён к другому ученику школы."


#: Корневой сертификат Минцифры: им подписан platform-api2.max.ru, а в
#: стандартном наборе certifi его нет (документация MAX: «добавить в доверенные»).
#: Доверие расширяем только для запросов к MAX, остальной сервер не трогаем.
MINCIFRY_ROOT = Path(__file__).resolve().parent.parent / "core" / "certs" / "russian_trusted_root_ca.pem"


@lru_cache(maxsize=1)
def max_ssl_context() -> ssl.SSLContext:
    """Набор certifi + корень Минцифры — для запросов к API MAX."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(MINCIFRY_ROOT))
    return ctx


class ChannelNotConfiguredError(RuntimeError):
    """Канал у экземпляра не настроен (нет ключей в .env)."""


class ChannelConflictError(RuntimeError):
    """Внешний аккаунт уже привязан к другому ученику."""


def configured(channel: Channel, settings: Optional[Settings] = None) -> bool:
    """Настроен ли канал: без ключей кнопку не показываем."""
    s = settings or Settings()
    if channel == "vk":
        return bool(s.vk_group_id and s.vk_group_token and s.vk_callback_secret)
    return bool(s.max_bot_token and s.max_bot_username and s.max_webhook_secret)


def sending_enabled(channel: Channel) -> bool:
    """Включён ли рубильник отправки в кабинете администратора."""
    return settings_store.get_bool(SETTING_KEY[channel])


def _link_url(channel: Channel, raw: str, s: Settings) -> str:
    if channel == "vk":
        return f"https://vk.me/club{s.vk_group_id.strip().lstrip('-')}?ref={raw}"
    return f"https://max.ru/{s.max_bot_username.strip().lstrip('@')}?start={raw}"


async def issue_link(user_id: int, channel: Channel, settings: Optional[Settings] = None) -> tuple[str, datetime]:
    """Выпустить ссылку привязки с одноразовым токеном.

    :raises ChannelNotConfiguredError: канал не настроен.
    :raises link_token_service.LinkTokenServiceUnavailableError: хранилище недоступно.
    """
    s = settings or Settings()
    if not configured(channel, s):
        raise ChannelNotConfiguredError(channel)
    raw, expires_at = await link_token_service.issue(get_redis(s.redis_url), user_id, TOKEN_KIND[channel])
    return _link_url(channel, raw, s), expires_at


@dataclass(frozen=True)
class LinkState:
    """Состояние канала для кабинета."""

    channel: Channel
    available: bool
    linked: bool


async def states(db: AsyncSession, user_id: int) -> list[LinkState]:
    """Что показать ученику: доступен ли канал и подключён ли он."""
    rows = (await db.execute(text(
        "SELECT channel FROM messenger_link WHERE user_id = :u AND disabled_at IS NULL"
    ), {"u": user_id})).scalars().all()
    s = Settings()
    return [LinkState(c, configured(c, s), c in rows) for c in CHANNELS]


async def bind(db: AsyncSession, channel: Channel, raw_token: str, external_id: str) -> int:
    """Погасить токен из ссылки и привязать внешний аккаунт к его владельцу.

    Повторная привязка после отписки снимает отписку.

    :returns: ID ученика.
    :raises link_token_service.LinkTokenError: токен неверный, истёк или не для канала.
    :raises ChannelConflictError: аккаунт уже привязан к другому ученику.
    """
    payload = await link_token_service.consume(get_redis(Settings().redis_url), raw_token)
    if payload.kind != TOKEN_KIND[channel]:
        raise link_token_service.LinkTokenError("invalid")
    owner = (await db.execute(text(
        "SELECT user_id FROM messenger_link WHERE channel = :c AND external_id = :e"
    ), {"c": channel, "e": external_id})).scalar()
    if owner is not None and owner != payload.user_id:
        raise ChannelConflictError(channel)
    await db.execute(text(
        "INSERT INTO messenger_link (user_id, channel, external_id) VALUES (:u, :c, :e) "
        "ON CONFLICT (user_id, channel) DO UPDATE SET external_id = EXCLUDED.external_id, "
        "created_at = now(), disabled_at = NULL, disabled_reason = NULL"
    ), {"u": payload.user_id, "c": channel, "e": external_id})
    await log_event(db, "messenger.linked", user_id=payload.user_id, details={"channel": channel})
    await db.commit()
    logger.info("tsk-1253: канал %s привязан к ученику %s", channel, payload.user_id)
    return payload.user_id


async def disable_by_external(db: AsyncSession, channel: Channel, external_id: str, reason: str) -> Optional[int]:
    """Отписать по внешнему id (слово «стоп», запрет сообщений, остановка бота).

    :returns: ID ученика или None, если привязки не было.
    """
    user_id = (await db.execute(text(
        "UPDATE messenger_link SET disabled_at = now(), disabled_reason = :r "
        "WHERE channel = :c AND external_id = :e AND disabled_at IS NULL RETURNING user_id"
    ), {"c": channel, "e": external_id, "r": reason})).scalar()
    if user_id is not None:
        await log_event(db, "messenger.disabled", user_id=user_id, details={"channel": channel, "reason": reason})
        logger.info("tsk-1253: канал %s ученика %s отключён (%s)", channel, user_id, reason)
    await db.commit()
    return user_id


async def disable_by_user(db: AsyncSession, user_id: int, channel: Channel) -> bool:
    """Отписка из кабинета. :returns: была ли активная привязка."""
    done = (await db.execute(text(
        "UPDATE messenger_link SET disabled_at = now(), disabled_reason = 'cabinet' "
        "WHERE user_id = :u AND channel = :c AND disabled_at IS NULL RETURNING id"
    ), {"u": user_id, "c": channel})).scalar()
    if done is not None:
        await log_event(db, "messenger.disabled", user_id=user_id, details={"channel": channel, "reason": "cabinet"})
    await db.commit()
    return done is not None


def is_stop_word(message: str) -> bool:
    """Просьба отписаться в переписке."""
    return message.strip().strip(".!«»\"").lower() in STOP_WORDS


class SendRefusedError(RuntimeError):
    """Сервис отказал: писать этому человеку больше нельзя."""


async def send_text(channel: Channel, external_id: str, message: str, settings: Optional[Settings] = None) -> None:
    """Отправить текст в канал.

    :raises SendRefusedError: получатель запретил сообщения.
    :raises httpx.HTTPError | RuntimeError: прочие сбои транспорта.
    """
    s = settings or Settings()
    verify: ssl.SSLContext | bool = max_ssl_context() if channel == "max" else True
    async with httpx.AsyncClient(timeout=10, verify=verify) as client:
        if channel == "vk":
            r = await client.post(VK_API, data={
                "user_id": external_id, "random_id": secrets.randbelow(2**31),
                "message": message, "access_token": s.vk_group_token, "v": VK_API_VERSION,
                "dont_parse_links": 0,
            })
            body = r.json()
            err = body.get("error")
            if err:
                if err.get("error_code") in VK_NO_PERMISSION_CODES:
                    raise SendRefusedError(f"vk {err.get('error_code')}")
                raise RuntimeError(f"vk error {err.get('error_code')}")
            return
        r = await client.post(
            f"{MAX_API}/messages", params={"user_id": external_id},
            json={"text": message}, headers={"Authorization": s.max_bot_token},
        )
        if r.status_code == 403:
            raise SendRefusedError("max 403")
        r.raise_for_status()


async def reply_safe(channel: Channel, external_id: str, message: str) -> None:
    """Ответ в переписке на событие (привязка, отписка); сбой только в лог."""
    try:
        await send_text(channel, external_id, message)
    except Exception as e:  # ответ вежливость, не обязательство
        logger.warning("tsk-1253: ответ в %s не отправлен: %s", channel, type(e).__name__)


async def notify_user(db: AsyncSession, *, user_id: int, kind: str, url: str) -> int:
    """Разослать ученику уведомление во все его включённые каналы ВК/MAX.

    :returns: сколько сообщений принято.
    """
    s = Settings()
    active = [c for c in CHANNELS if configured(c, s) and sending_enabled(c)]
    if not active:
        return 0
    try:
        rows = (await db.execute(text(
            "SELECT channel, external_id FROM messenger_link "
            "WHERE user_id = :u AND disabled_at IS NULL AND channel = ANY(:cs)"
        ), {"u": user_id, "cs": active})).all()
    except Exception:
        logger.exception("tsk-1253: не прочитаны каналы ученика %s", user_id)
        await db.rollback()
        return 0
    sent = 0
    for channel, external_id in rows:
        try:
            await send_text(channel, external_id, _TEXTS[kind].format(url=url) + _FOOTER, s)
            sent += 1
        except SendRefusedError:
            await disable_by_external(db, channel, external_id, "refused")
        except Exception as e:
            logger.error("tsk-1253: %s для ученика %s не отправлено: %s", channel, user_id, type(e).__name__)
    return sent


_STAFF_SENDER_SQL = """
SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
WHERE ur.user_id = :s AND r.name <> 'student'
LIMIT 1
"""

#: Сильные ссылки на фоновые задачи (документация asyncio.create_task).
_background: set[asyncio.Task] = set()


def schedule_teacher_message(*, sender_id: Optional[int], recipient_id: int, url: str) -> None:
    """Уведомить ученика о сообщении сотрудника фоном, своей сессией.

    Системные сообщения и переписка учеников между собой не дублируются.
    Оба рубильника выключены — выход сразу, без задачи.
    """
    if sender_id is None or sender_id == recipient_id:
        return
    if not any(sending_enabled(c) for c in CHANNELS):
        return

    async def _run() -> None:
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            try:
                staff = (await db.execute(text(_STAFF_SENDER_SQL), {"s": sender_id})).first()
            except Exception:
                logger.exception("tsk-1253: не проверен отправитель %s", sender_id)
                return
            if staff is not None:
                await notify_user(db, user_id=recipient_id, kind=KIND_MESSAGE, url=url)

    task = asyncio.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)
