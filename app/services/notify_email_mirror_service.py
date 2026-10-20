"""Дубль уведомлений ученику на почту (tsk-1253).

**Зачем.** На экземплярах без бота (пилот «Дипломат») и у учеников без
привязанного Telegram (на основной школе `tg_id` есть у 4 из 182) напоминание
и сообщение преподавателя видны только в кабинете — тот, кто пропал, их не
увидит. Почта есть почти у всех (88 из 102 активных учеников, замер 20.10).

**Что шлёт.** Короткое письмо «у вас новое в кабинете» со ссылкой, БЕЗ текста
сообщения: переписка остаётся в кабинете, а в почтовом ящике не оседают
персональные данные и подробности учёбы (решение оператора 2026-10-20).

**Когда.** Только при включённом рубильнике `notify_email_mirror_enabled`
(кабинет администратора, выключен по умолчанию — письма живым людям), только
ученику и не чаще одного письма в час на человека. Отметка об отправке — строка
`audit_event` (`email.mirror.sent`): журнал общий для всех worker'ов и
переживает перезапуск, а значит и окно «час» держится честно.

Никогда не бросает исключение: письмо — дубль, его сбой не должен ронять
отправку сообщения или проход напоминаний.
"""
from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings_store
from app.core.config import Settings
from app.services.audit_service import log_event
from app.services.email_transport import EmailSendError, email_configured, send_html_email

logger = logging.getLogger(__name__)

SETTING_KEY = "notify_email_mirror_enabled"
SENT_EVENT = "email.mirror.sent"
#: Не чаще одного письма в час на ученика (решение оператора 2026-10-20).
THROTTLE_MINUTES = 60

KIND_REENGAGE = "reengage"
KIND_MESSAGE = "message"

_SUBJECTS = {
    KIND_REENGAGE: "Продолжим учиться?",
    KIND_MESSAGE: "Новое сообщение от преподавателя",
}
_LEADS = {
    KIND_REENGAGE: "Вы давно не заглядывали в кабинет. Следующее задание уже ждёт.",
    KIND_MESSAGE: "Преподаватель написал вам сообщение. Прочитать его можно в кабинете.",
}
_BUTTONS = {
    KIND_REENGAGE: "Продолжить учиться",
    KIND_MESSAGE: "Открыть сообщения",
}

_RECIPIENT_SQL = """
SELECT u.email
FROM users u
WHERE u.id = :u
  AND u.is_active
  AND u.merged_into_user_id IS NULL
  AND u.blocked_at IS NULL
  AND u.email IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
      WHERE ur.user_id = u.id AND r.name = 'student'
  )
"""

_RECENT_SQL = """
SELECT 1 FROM audit_event
WHERE user_id = :u AND event_type = :ev
  AND ts > now() - make_interval(mins => :mins)
LIMIT 1
"""


def mirror_enabled() -> bool:
    """Включён ли дубль на почту в кабинете администратора."""
    return settings_store.get_bool(SETTING_KEY)


def _render_html(kind: str, url: str) -> str:
    """Письмо из контролируемых строк: пользовательского текста в нём нет."""
    safe_url = escape(url, quote=True)
    return (
        "<p>Здравствуйте!</p>"
        f"<p>{_LEADS[kind]}</p>"
        f'<p><a href="{safe_url}">{_BUTTONS[kind]}</a></p>'
        "<p style=\"color:#888;font-size:12px\">Письмо отправлено автоматически, "
        "отвечать на него не нужно.</p>"
    )


async def mirror_to_email(
    db: AsyncSession,
    *,
    user_id: int,
    kind: str,
    url: str,
    settings: Optional[Settings] = None,
) -> bool:
    """Отправить ученику письмо-дубль, если это разрешено и пора.

    :param kind: `reengage` | `message`.
    :param url: куда ведёт кнопка письма.
    :returns: True, если письмо принято транспортом.
    """
    try:
        if not mirror_enabled():
            return False
        settings = settings or Settings()
        if not email_configured(settings):
            logger.warning("tsk-1253: почта не настроена — дубль для %s не отправлен", user_id)
            return False
        email = (await db.execute(text(_RECIPIENT_SQL), {"u": user_id})).scalar()
        if not email:
            return False
        recent = (await db.execute(text(_RECENT_SQL), {
            "u": user_id, "ev": SENT_EVENT, "mins": THROTTLE_MINUTES,
        })).first()
        if recent is not None:
            logger.info("tsk-1253: письмо ученику %s уже было за последний час — пропуск", user_id)
            return False
        await send_html_email(settings, email, _SUBJECTS[kind], _render_html(kind, url))
        await log_event(db, SENT_EVENT, user_id=user_id, details={"kind": kind})
        await db.commit()
        return True
    except EmailSendError:
        logger.error("tsk-1253: транспорт не принял письмо-дубль для %s", user_id)
        return False
    except Exception:
        logger.exception("tsk-1253: дубль на почту для %s упал", user_id)
        # Сессия после сбоя SQL непригодна: без отката следующий ученик в цикле
        # прохода (или вызывающий код) получил бы «transaction is aborted».
        await db.rollback()
        return False


def cabinet_url(path: str, settings: Optional[Settings] = None) -> str:
    """Абсолютная ссылка в кабинет ученика."""
    base = (settings or Settings()).public_base_url.rstrip("/")
    return f"{base}{path}"


_STAFF_SENDER_SQL = """
SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
WHERE ur.user_id = :s AND r.name <> 'student'
LIMIT 1
"""


async def mirror_teacher_message(
    db: AsyncSession, *, sender_id: Optional[int], recipient_id: int,
) -> bool:
    """Дубль на почту сообщения сотрудника школы ученику.

    Системные сообщения (без отправителя) и переписка учеников между собой
    не дублируются: письмо — про то, что ученику написал живой человек.
    """
    if not mirror_enabled() or sender_id is None or sender_id == recipient_id:
        return False
    try:
        staff = (await db.execute(text(_STAFF_SENDER_SQL), {"s": sender_id})).first()
    except Exception:
        logger.exception("tsk-1253: не удалось проверить отправителя %s", sender_id)
        await db.rollback()
        return False
    if staff is None:
        return False
    return await mirror_to_email(
        db, user_id=recipient_id, kind=KIND_MESSAGE, url=cabinet_url("/me/messages"),
    )


#: Сильные ссылки на фоновые задачи: иначе сборщик мусора может снять задачу
#: до завершения (документация asyncio.create_task).
_background: set[asyncio.Task] = set()


def schedule_teacher_message_mirror(*, sender_id: Optional[int], recipient_id: int) -> None:
    """Отправить дубль сообщения фоном, своей сессией.

    Ответ преподавателю не ждёт SMTP (до 10 с), а сбой письма не трогает его
    сессию. Выключенный рубильник — выход сразу, без задачи.
    """
    if not mirror_enabled() or sender_id is None or sender_id == recipient_id:
        return

    async def _run() -> None:
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            await mirror_teacher_message(db, sender_id=sender_id, recipient_id=recipient_id)

    task = asyncio.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)
