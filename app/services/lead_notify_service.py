"""tsk-1407: уведомление маркетологов о новой заявке с сайта, пришедшей через бота.

Заявка из бота записи (кнопка «Написать в Telegram» на лендинге) — живой человек,
который ждёт ответа. Маркетолог узнаёт о ней сразу, тем же способом, что о новой
регистрации (tsk-1338): уведомление во «Входящие» кабинета.

Сбой уведомления не должен ронять приём заявки: всё — в savepoint, ошибка
логируется и проглатывается только здесь.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import inbox_service
from app.services.registration_notify_service import _list_marketer_user_ids

logger = logging.getLogger(__name__)

NOTIFICATION_KIND = "lead_created"


async def notify_marketers_new_lead(
    db: AsyncSession,
    *,
    lead_id: int,
    full_name: Optional[str],
    contact: str,
    page: Optional[str],
) -> int:
    """Создать уведомление `lead_created` каждому маркетологу.

    :returns: число созданных уведомлений (0 при ошибке или без маркетологов).
    """
    try:
        async with db.begin_nested():
            marketer_ids = await _list_marketer_user_ids(db)
            if not marketer_ids:
                logger.info("tsk-1407: маркетологов нет — о заявке %s сообщать некому", lead_id)
                return 0
            who = full_name or contact
            where = f" со страницы «{page}»" if page else ""
            payload = {"lead_id": int(lead_id), "page": page}
            for mid in marketer_ids:
                await inbox_service.create_for_user(
                    db,
                    user_id=mid,
                    kind=NOTIFICATION_KIND,
                    title="Новая заявка с сайта",
                    content=f"{who} оставил(а) заявку через Telegram{where}. Контакт: {contact}.",
                    payload=payload,
                    created_by=None,
                )
            return len(marketer_ids)
    except Exception:  # noqa: BLE001 — приём заявки важнее уведомления
        logger.exception("tsk-1407: не удалось уведомить маркетологов о заявке lead_id=%s", lead_id)
        return 0
