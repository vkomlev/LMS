"""Единая точка отправки писем LMS (tsk-1340).

Два транспорта, выбор — `EMAIL_BACKEND`:
- `smtp`  — локальный Postfix на сервере платформы (письма не уходят за рубеж);
- `resend` — Resend API (прежний путь, оставлен для отката).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(RuntimeError):
    """Транспорт не принял письмо."""


def email_configured(settings: Settings) -> bool:
    """Настроен ли транспорт: SMTP — всегда (локальный сервер), Resend — при наличии ключа."""
    if settings.email_backend == "smtp":
        return bool(settings.smtp_host)
    return bool(settings.resend_api_key)


def _build_message(settings: Settings, to: str, subject: str, html: str) -> EmailMessage:
    """Собрать MIME-письмо с HTML-частью и обязательными заголовками."""
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1].strip("> "))
    msg.set_content("Откройте письмо в программе с поддержкой HTML.")
    msg.add_alternative(html, subtype="html")
    return msg


def _smtp_send_sync(settings: Settings, msg: EmailMessage) -> None:
    """Синхронная отправка через SMTP (выполняется в отдельном потоке)."""
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)


async def send_html_email(settings: Settings, to: str, subject: str, html: str) -> None:
    """Отправить HTML-письмо выбранным транспортом.

    Raises:
        EmailSendError: транспорт вернул ошибку или недоступен.
    """
    if settings.email_backend == "smtp":
        msg = _build_message(settings, to, subject, html)
        try:
            await asyncio.to_thread(_smtp_send_sync, settings, msg)
        except (smtplib.SMTPException, OSError) as e:
            logger.error("SMTP ошибка отправки: %s", e)
            raise EmailSendError("Ошибка отправки email") from e
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={"from": settings.smtp_from, "to": [to], "subject": subject, "html": html},
            )
    except httpx.HTTPError as e:
        logger.error("Resend недоступен: %s", e)
        raise EmailSendError("Ошибка отправки email") from e
    if resp.status_code >= 400:
        logger.error("Resend API error %s: %s", resp.status_code, resp.text[:300])
        raise EmailSendError("Ошибка отправки email")
