"""NotificationEmailService — best-effort email-уведомления ученикам (Phase Y-4).

Wrapper над Resend API в стиле magic_link_service.send_magic_link_email.
Не raises — на любую ошибку транспорта возвращает False; caller записывает
audit_event 'email.failed' и продолжает.

Шаблон письма (SA_COM graded): plain text + минимальный HTML, RU,
ссылка на /me/history через settings.public_base_url.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"


def _build_history_url(public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/me/history"


def _render_subject(task_title: Optional[str]) -> str:
    if task_title:
        return f"Преподаватель оценил вашу попытку — {task_title}"
    return "Преподаватель оценил вашу попытку"


def _render_html(
    *,
    task_title: Optional[str],
    score: int,
    max_score: int,
    comment: Optional[str],
    history_url: str,
) -> str:
    """Минимальный HTML без user-input в шаблоне (только из контролируемых полей)."""
    title_safe = task_title or "(без названия)"
    comment_block = ""
    if comment:
        # Резка: backend не валидирует семантику, но email лучше не разбухать.
        clipped = comment[:1024]
        comment_block = (
            f"<p><strong>Комментарий преподавателя:</strong></p>"
            f"<p>{clipped}</p>"
        )
    return (
        f"<p>Здравствуйте,</p>"
        f"<p>Преподаватель оценил вашу попытку по задаче «{title_safe}»:</p>"
        f"<p><strong>Балл: {score} из {max_score}</strong></p>"
        f"{comment_block}"
        f"<p><a href=\"{history_url}\">Открыть историю попыток</a></p>"
    )


async def send_sa_com_graded(
    *,
    recipient_email: str,
    task_title: Optional[str],
    score: int,
    max_score: int,
    comment: Optional[str],
    settings: Settings,
) -> bool:
    """Отправить email ученику об оценке SA_COM.

    Возвращает True при success (Resend 2xx), False при любой ошибке транспорта.
    Никогда не raises — ошибка не должна валить grade-операцию.
    """
    if not settings.resend_api_key:
        logger.warning(
            "RESEND_API_KEY не задан — письмо SA_COM graded не отправлено для %s",
            recipient_email,
        )
        return False

    history_url = _build_history_url(settings.public_base_url)
    subject = _render_subject(task_title)
    html = _render_html(
        task_title=task_title,
        score=score,
        max_score=max_score,
        comment=comment,
        history_url=history_url,
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.smtp_from,
                    "to": [recipient_email],
                    "subject": subject,
                    "html": html,
                },
            )
        if resp.status_code >= 400:
            logger.error(
                "Resend API error %s при отправке SA_COM graded для %s: %s",
                resp.status_code, recipient_email, resp.text[:300],
            )
            return False
        return True
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.exception(
            "Исключение при отправке SA_COM graded email для %s: %s",
            recipient_email, e,
        )
        return False
