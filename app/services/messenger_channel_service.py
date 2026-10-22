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
import json
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
# tsk-1398: остальные уведомления ученику (решение оператора 21.10). В тексте —
# только повод и ссылка: сумм, оценок и комментариев в мессенджер не шлём.
KIND_PAYMENT = "payment"
KIND_HELP = "help"
KIND_REVIEW = "review"
_TEXTS = {
    KIND_REENGAGE: "Вы давно не заглядывали в курс. Следующее задание уже ждёт: {url}",
    KIND_MESSAGE: "Преподаватель написал вам сообщение. Прочитать: {url}",
    KIND_PAYMENT: "Напоминаем об оплате обучения. Подробности в кабинете: {url}",
    KIND_HELP: "Преподаватель ответил на вашу просьбу о помощи. Посмотреть: {url}",
    KIND_REVIEW: "Преподаватель проверил вашу работу. Результат в кабинете: {url}",
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


def any_sending_enabled() -> bool:
    """Включён ли хоть один внешний канал (ВК, MAX, Telegram)."""
    return any(sending_enabled(c) for c in CHANNELS) or settings_store.get_bool(TG_SETTING_KEY)


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

    channel: str
    available: bool
    linked: bool


async def states(db: AsyncSession, user_id: int) -> list[LinkState]:
    """Что показать ученику: доступен ли канал и подключён ли он."""
    rows = (await db.execute(text(
        "SELECT channel FROM messenger_link WHERE user_id = :u AND disabled_at IS NULL"
    ), {"u": user_id})).scalars().all()
    s = Settings()
    out = [LinkState(c, configured(c, s), c in rows) for c in CHANNELS]
    # tsk-1398: Telegram — для кнопки «Отключить уведомления». Подключается он
    # своей кнопкой (ссылка на бота, tsk-1253 этап А).
    tg_id = (await db.execute(text("SELECT tg_id FROM users WHERE id = :u"), {"u": user_id})).scalar()
    out.append(LinkState("tg", tg_configured(s), bool(tg_id) and not await tg_opted_out(db, user_id)))
    return out


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


def _vk_keyboard(buttons: list[str]) -> str:
    """Встроенная клавиатура ВК: нажатие присылает текст кнопки как сообщение."""
    rows = [[{"action": {"type": "text", "label": label[:40]}}] for label in buttons]
    return json.dumps({"inline": True, "buttons": rows}, ensure_ascii=False)


def _max_keyboard(buttons: list[str]) -> list[dict]:
    """Клавиатура MAX: кнопка типа message отправляет свой текст от пользователя."""
    rows = [[{"type": "message", "text": label}] for label in buttons]
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


async def send_text(
    channel: Channel,
    external_id: str,
    message: str,
    settings: Optional[Settings] = None,
    buttons: Optional[list[str]] = None,
) -> None:
    """Отправить текст в канал (с кнопками-ответами, если заданы).

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
                **({"keyboard": _vk_keyboard(buttons)} if buttons else {}),
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
            json={"text": message, **({"attachments": _max_keyboard(buttons)} if buttons else {})},
            headers={"Authorization": s.max_bot_token},
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
    sent = await _notify_tg(db, user_id=user_id, message=_TEXTS[kind].format(url=url), settings=s)
    if not active:
        return sent
    try:
        rows = (await db.execute(text(
            "SELECT channel, external_id FROM messenger_link "
            "WHERE user_id = :u AND disabled_at IS NULL AND channel = ANY(:cs)"
        ), {"u": user_id, "cs": active})).all()
    except Exception:
        logger.exception("tsk-1253: не прочитаны каналы ученика %s", user_id)
        await db.rollback()
        return sent
    for channel, external_id in rows:
        try:
            await send_text(channel, external_id, _TEXTS[kind].format(url=url) + _FOOTER, s)
            sent += 1
        except SendRefusedError:
            await disable_by_external(db, channel, external_id, "refused")
        except Exception as e:
            logger.error("tsk-1253: %s для ученика %s не отправлено: %s", channel, user_id, type(e).__name__)
    return sent


TG_API = "https://api.telegram.org"
TG_SETTING_KEY = "notify_tg_enabled"


async def tg_opted_out(db: AsyncSession, user_id: int) -> bool:
    """Ученик отписался от уведомлений в Telegram."""
    return (await db.execute(text(
        "SELECT 1 FROM messenger_link WHERE user_id = :u AND channel = 'tg' AND disabled_at IS NOT NULL"
    ), {"u": user_id})).first() is not None


async def set_tg_optout(db: AsyncSession, user_id: int, reason: str) -> None:
    """Отписать от Telegram (слово «стоп», остановка бота, кабинет). Коммитит."""
    tg_id = (await db.execute(text("SELECT tg_id FROM users WHERE id = :u"), {"u": user_id})).scalar()
    await db.execute(text(
        "INSERT INTO messenger_link (user_id, channel, external_id, disabled_at, disabled_reason) "
        "VALUES (:u, 'tg', :e, now(), :r) "
        "ON CONFLICT (user_id, channel) DO UPDATE SET external_id = EXCLUDED.external_id, "
        "disabled_at = now(), disabled_reason = EXCLUDED.disabled_reason"
    ), {"u": user_id, "e": str(tg_id or f"user-{user_id}"), "r": reason})
    await log_event(db, "messenger.disabled", user_id=user_id, details={"channel": "tg", "reason": reason})
    await db.commit()
    logger.info("tsk-1398: Telegram ученика %s отключён (%s)", user_id, reason)


async def clear_tg_optout(db: AsyncSession, user_id: int) -> None:
    """Новая привязка Telegram снимает отписку. Не коммитит."""
    await db.execute(text("DELETE FROM messenger_link WHERE user_id = :u AND channel = 'tg'"), {"u": user_id})


def tg_configured(settings: Optional[Settings] = None) -> bool:
    """Есть ли у LMS токен ученического бота Telegram."""
    return bool((settings or Settings()).student_bot_token)


async def _notify_tg(db: AsyncSession, *, user_id: int, message: str, settings: Settings) -> int:
    """Telegram: получатель — `users.tg_id` (привязка этапа А tsk-1253).

    Своя ветка: привязка TG живёт в identity, а не в `messenger_link`.
    Рубильник `notify_tg_enabled` выключен по умолчанию.
    """
    if not (tg_configured(settings) and settings_store.get_bool(TG_SETTING_KEY)):
        return 0
    try:
        tg_id = (await db.execute(text(
            "SELECT tg_id FROM users WHERE id = :u AND is_active AND blocked_at IS NULL"
        ), {"u": user_id})).scalar()
    except Exception:
        logger.exception("tsk-1398: не прочитан Telegram ученика %s", user_id)
        await db.rollback()
        return 0
    if not tg_id or await tg_opted_out(db, user_id):
        return 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TG_API}/bot{settings.student_bot_token}/sendMessage",
                json={"chat_id": tg_id, "text": message + _FOOTER},
            )
        if r.status_code == 403:
            # 403 бывает и «заблокировал бота», и «ни разу не запускал». Отписка —
            # только в первом случае: второму поможет запуск бота по ссылке.
            if "blocked" in (r.json().get("description") or "").lower():
                await set_tg_optout(db, user_id, "bot_stopped")
            else:
                logger.info("tsk-1398: ученик %s не запускал бота Telegram", user_id)
            return 0
        r.raise_for_status()
        return 1
    except Exception as e:
        # Имя класса, не текст: в тексте ошибки httpx адрес с токеном бота.
        logger.error("tsk-1398: Telegram ученику %s не отправлено: %s", user_id, type(e).__name__)
        return 0


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
    if not any_sending_enabled():
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


#: Какие уведомления кабинета дублировать во внешние каналы и куда вести
#: ссылку (решение оператора 21.10, tsk-1398).
INBOX_KIND_MAP: dict[str, tuple[str, str]] = {
    "payment_overdue": (KIND_PAYMENT, "/me/payments"),
    "help_request_replied": (KIND_HELP, "/me/notifications"),
    "help_request_closed": (KIND_HELP, "/me/notifications"),
    "sa_com_graded": (KIND_REVIEW, "/me/notifications"),
    "task_returned_for_rework": (KIND_REVIEW, "/me/notifications"),
    "sa_com_regraded": (KIND_REVIEW, "/me/notifications"),
}

_PENDING_KEY = "tsk1398_external_notify"


def queue_for_inbox(db: AsyncSession, *, user_id: int, inbox_kind: str) -> None:
    """Запомнить уведомление кабинета для отправки во внешние каналы.

    Отправка — только после фиксации транзакции: уведомление, откатившееся
    вместе с оценкой или оплатой, наружу уйти не должно.
    """
    mapped = INBOX_KIND_MAP.get(inbox_kind)
    if mapped is None or not any_sending_enabled():
        return
    db.sync_session.info.setdefault(_PENDING_KEY, []).append((user_id, *mapped))


def _dispatch(user_id: int, kind: str, path: str) -> None:
    """Отправить фоном, своей сессией."""
    base = Settings().public_base_url.rstrip("/")

    async def _run() -> None:
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            await notify_user(db, user_id=user_id, kind=kind, url=f"{base}{path}")

    task = asyncio.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)


def _after_commit(session) -> None:  # noqa: ANN001 - sync Session из события
    for user_id, kind, path in session.info.pop(_PENDING_KEY, []):
        try:
            _dispatch(user_id, kind, path)
        except RuntimeError:
            # Нет работающего цикла событий (синхронный скрипт) — наружу не шлём.
            logger.warning("tsk-1398: нет цикла событий, уведомление %s не отправлено", kind)


def _after_rollback(session) -> None:  # noqa: ANN001
    session.info.pop(_PENDING_KEY, None)


def _install_session_hooks() -> None:
    """Подписать отправку на фиксацию и откат любой сессии (один раз)."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_rollback", _after_rollback)


_install_session_hooks()


# ─── Этап 3: ответы ученика из мессенджера — в историю переписки ──────────

_LINKED_STUDENT_SQL = """
SELECT user_id FROM messenger_link
WHERE channel = :c AND external_id = :e AND disabled_at IS NULL
"""

#: Последнее сообщение сотрудника ученику: ответ из мессенджера продолжает его
#: ветку (срочное сообщение, ответ на заявку, обычная переписка).
_LAST_STAFF_MESSAGE_SQL = """
SELECT m.id, m.sender_id FROM messages m
WHERE m.recipient_id = :u AND m.sender_id IS NOT NULL AND m.sender_id <> :u
  AND EXISTS (
      SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
      WHERE ur.user_id = m.sender_id AND r.name <> 'student'
  )
ORDER BY m.sent_at DESC, m.id DESC
LIMIT 1
"""

_CURATOR_SQL = """
SELECT curator_id FROM student_curator
WHERE student_id = :u AND ended_at IS NULL
ORDER BY assigned_at DESC LIMIT 1
"""

NO_ADDRESSEE_TEXT = (
    "Сообщение не доставлено: не нашли, кому из преподавателей его передать. "
    "Напишите, пожалуйста, в кабинете ученика."
)


async def linked_student(db: AsyncSession, channel: Channel, external_id: str) -> Optional[int]:
    """Ученик, подключивший этот внешний аккаунт (активная привязка)."""
    return (await db.execute(text(_LINKED_STUDENT_SQL), {"c": channel, "e": external_id})).scalar()


async def store_inbound(db: AsyncSession, *, student_id: int, channel: Channel, body: str) -> Optional[int]:
    """Сохранить сообщение ученика из мессенджера в переписку кабинета.

    Адресат — сотрудник, писавший ученику последним (ответ ложится в его
    ветку), иначе текущий куратор. Никого нет — не сохраняем, ученику ответ.

    :returns: id сообщения или None.
    """
    from app.services.messages_service import MessagesService

    last = (await db.execute(text(_LAST_STAFF_MESSAGE_SQL), {"u": student_id})).first()
    reply_to_id, recipient_id = (last[0], last[1]) if last else (None, None)
    if recipient_id is None:
        recipient_id = (await db.execute(text(_CURATOR_SQL), {"u": student_id})).scalar()
    if recipient_id is None:
        return None
    msg = await MessagesService().send_message(
        db, message_type="text", content={"text": body[:4000]},
        recipient_id=int(recipient_id), sender_id=student_id,
        source_system=channel, reply_to_id=reply_to_id,
    )
    await db.commit()
    logger.info("tsk-1398: ответ ученика %s из %s сохранён (сообщение %s)", student_id, channel, msg.id)
    return msg.id


# ─── Этап 5: экстренная связь — полный текст во все каналы ученика ────────

URGENT_PREFIX = "Срочное сообщение от преподавателя"


async def student_channels(db: AsyncSession, user_id: int) -> list[tuple[str, str]]:
    """Активные внешние каналы ученика: [(канал, внешний id)], Telegram — по tg_id."""
    s = Settings()
    rows = (await db.execute(text(
        "SELECT channel, external_id FROM messenger_link WHERE user_id = :u AND disabled_at IS NULL "
        "ORDER BY channel"
    ), {"u": user_id})).all()
    out = [(c, e) for c, e in rows if configured(c, s)]
    out = [(c, e) for c, e in out if c != "tg"]
    if tg_configured(s) and not await tg_opted_out(db, user_id):
        tg_id = (await db.execute(text("SELECT tg_id FROM users WHERE id = :u"), {"u": user_id})).scalar()
        if tg_id:
            out.append(("tg", str(tg_id)))
    return out


async def send_urgent(db: AsyncSession, *, student_id: int, sender_name: str, body: str) -> list[str]:
    """Отправить текст преподавателя целиком во все каналы ученика.

    Рубильники рассылки здесь не спрашиваются: это не рассылка, а адресное
    сообщение живого человека, отправленное его нажатием. Отписку уважаем —
    отключённые каналы в список не попадают.

    :returns: каналы, принявшие сообщение.
    """
    s = Settings()
    message = f"{URGENT_PREFIX} ({sender_name}):\n\n{body}\n\nОтветить можно прямо здесь."
    delivered: list[str] = []
    for channel, external_id in await student_channels(db, student_id):
        try:
            if channel == "tg":
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(
                        f"{TG_API}/bot{s.student_bot_token}/sendMessage",
                        json={"chat_id": int(external_id), "text": message},
                    )
                r.raise_for_status()
            else:
                await send_text(channel, external_id, message, s)
            delivered.append(channel)
        except SendRefusedError:
            await disable_by_external(db, channel, external_id, "refused")
        except Exception as e:
            logger.error("tsk-1398: срочное в %s ученику %s не ушло: %s", channel, student_id, type(e).__name__)
    return delivered


async def send_tg_with_buttons(chat_id: int, message: str, buttons: list[str]) -> int:
    """Telegram: обычная клавиатура (кнопка присылает свой текст боту).

    Ответ приходит в ученического бота (TG_LMS), не в LMS: приём ответов из
    Telegram — доработка бота. Без токена — 0.
    """
    s = Settings()
    if not tg_configured(s):
        return 0
    keyboard = {"keyboard": [[{"text": b}] for b in buttons], "one_time_keyboard": True, "resize_keyboard": True}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{TG_API}/bot{s.student_bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "reply_markup": keyboard},
        )
    if r.status_code == 403:
        return 0
    r.raise_for_status()
    return 1


async def route_inbound(db: AsyncSession, *, channel: Channel, external_id: str, body: str) -> None:
    """Входящее сообщение подключённого ученика: ответ на вопрос о занятии
    или обычное сообщение — в переписку. Неподключённым — ничего."""
    from app.services import absence_poll_service

    student_id = await linked_student(db, channel, external_id)
    if student_id is None or not body.strip():
        return
    reply = await absence_poll_service.handle_answer(db, student_id=student_id, channel=channel, body=body)
    if reply is None:
        if await store_inbound(db, student_id=student_id, channel=channel, body=body) is None:
            reply = NO_ADDRESSEE_TEXT
    if reply:
        await reply_safe(channel, external_id, reply)


async def route_tg_inbound(db: AsyncSession, *, tg_id: int, body: str) -> Optional[str]:
    """Сообщение ученика в боте Telegram (пересылает TG_LMS).

    :returns: что ответить в боте, или None — молчать.
    """
    from app.services import absence_poll_service

    student_id = (await db.execute(text(
        "SELECT id FROM users WHERE tg_id = :t AND is_active AND merged_into_user_id IS NULL "
        "ORDER BY id LIMIT 1"
    ), {"t": tg_id})).scalar()
    if student_id is None or not body.strip():
        return None
    if is_stop_word(body):
        await set_tg_optout(db, student_id, "stop_word")
        return STOPPED_TEXT
    reply = await absence_poll_service.handle_answer(db, student_id=student_id, channel="tg", body=body)
    if reply is not None:
        return reply
    if await store_inbound(db, student_id=student_id, channel="tg", body=body) is None:
        return NO_ADDRESSEE_TEXT
    return None
