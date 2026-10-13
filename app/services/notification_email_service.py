"""NotificationEmailService — best-effort email-уведомления ученикам (Phase Y-4).

Письма-уведомления поверх app.services.email_transport в стиле magic_link_service.send_magic_link_email.
Не raises — на любую ошибку транспорта возвращает False; caller записывает
audit_event 'email.failed' и продолжает.

Шаблон письма (SA_COM graded): plain text + минимальный HTML, RU,
ссылка на /me/history через settings.public_base_url.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional


from app.core.config import Settings
from app.services.email_transport import (
    EmailSendError,
    email_configured,
    send_html_email,
)

logger = logging.getLogger(__name__)



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

    Возвращает True при success (транспорт принял письмо), False при любой ошибке транспорта.
    Никогда не raises — ошибка не должна валить grade-операцию.
    """
    if not email_configured(settings):
        logger.warning(
            "Почтовый транспорт не настроен — письмо SA_COM graded не отправлено для %s",
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
        await send_html_email(settings, recipient_email, subject, html)
        return True
    except EmailSendError:
        logger.error("Не удалось отправить SA_COM graded для %s", recipient_email)
        return False


# --- tsk-010: напоминание о просроченной оплате -----------------------------


def _render_overdue_html(
    *,
    full_name: Optional[str],
    period: date,
    group_name: str,
    due_minor: int,
    payments_url: str,
) -> str:
    """Письмо о долге.

    Тон намеренно спокойный: человек мог просто забыть, а не отказаться платить.
    Никаких угроз доступом — по решению оператора доступ за неоплату не
    закрывается, и письмо не должно обещать того, чего система не делает.
    """
    greeting = f"Здравствуйте, {full_name}!" if full_name else "Здравствуйте!"
    amount = f"{due_minor // 100} ₽" if due_minor % 100 == 0 else f"{due_minor / 100:.2f} ₽"
    return (
        f"<p>{greeting}</p>"
        f"<p>Напоминаем про оплату обучения за {period:%m.%Y} "
        f"(направление «{group_name}»).</p>"
        f"<p><strong>Осталось оплатить: {amount}</strong></p>"
        f"<p>Оплатить картой или приложить чек можно в личном кабинете:</p>"
        f'<p><a href="{payments_url}">{payments_url}</a></p>'
        f"<p>Если вы уже оплатили — спасибо, письмо можно не читать: "
        f"оплата отражается в кабинете после подтверждения.</p>"
    )


async def send_payment_overdue(
    *,
    recipient_email: str,
    full_name: Optional[str],
    period: date,
    group_name: str,
    due_minor: int,
    settings: Settings,
) -> bool:
    """Отправить напоминание о долге. Как и остальные письма — best-effort.

    `False` вместо исключения: неудачная отправка одному человеку не должна
    обрывать рассылку остальным.
    """
    if not email_configured(settings):
        logger.warning(
            "Почтовый транспорт не настроен — напоминание об оплате не отправлено для %s",
            recipient_email,
        )
        return False
    payments_url = f"{settings.public_base_url.rstrip('/')}/me/payments"
    html = _render_overdue_html(
        full_name=full_name,
        period=period,
        group_name=group_name,
        due_minor=due_minor,
        payments_url=payments_url,
    )
    try:
        await send_html_email(
            settings, recipient_email, f"Оплата обучения за {period:%m.%Y}", html
        )
        return True
    except EmailSendError:
        logger.error("Не удалось отправить напоминание об оплате")
        return False
