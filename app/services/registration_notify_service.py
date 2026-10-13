"""tsk-1338: уведомление маркетологов о новой регистрации.

Маркетолог хочет видеть приток учеников сразу, а не выгрузкой из БД. Каждый
путь регистрации (magic-link, VK, Telegram) после создания пользователя
вызывает `notify_marketers_new_user` в той же транзакции.

Сбой уведомления НЕ должен ломать регистрацию: всё выполняется в savepoint
(`db.begin_nested()`), ошибка логируется и проглатывается только здесь —
с полным стеком в логе.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import inbox_service, notification_names

logger = logging.getLogger(__name__)

NOTIFICATION_KIND = "user_registered"

#: Как назвать канал регистрации человеку.
_VIA_HUMAN: dict[str, str] = {
    "magic_link": "ссылку на почту",
    "vk": "ВКонтакте",
    "tg": "Telegram",
}


async def _list_marketer_user_ids(db: AsyncSession) -> list[int]:
    """Все user_id с ролью marketer (тот же приём, что у методистов)."""
    res = await db.execute(
        text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE r.name = 'marketer'"
        )
    )
    return [int(row[0]) for row in res.fetchall()]


async def notify_marketers_new_user(db: AsyncSession, user_id: int, via: str) -> int:
    """Создать уведомление `user_registered` каждому маркетологу.

    :param user_id: id только что зарегистрированного пользователя.
    :param via: канал — `magic_link` | `vk` | `tg`.
    :returns: число созданных уведомлений (0 при ошибке или без маркетологов).
    """
    try:
        async with db.begin_nested():
            marketer_ids = await _list_marketer_user_ids(db)
            if not marketer_ids:
                logger.info("tsk-1338: маркетологов нет — о регистрации %s сообщать некому", user_id)
                return 0
            name = await notification_names.user_display_name(db, user_id)
            via_human = _VIA_HUMAN.get(via, via)
            payload = {"student_id": int(user_id), "student_name": name, "via": via}
            for mid in marketer_ids:
                await inbox_service.create_for_user(
                    db,
                    user_id=mid,
                    kind=NOTIFICATION_KIND,
                    title="Новая регистрация",
                    content=f"{name} зарегистрировался(-ась) через {via_human}.",
                    payload=payload,
                    created_by=None,
                )
            return len(marketer_ids)
    except Exception:  # noqa: BLE001 — регистрация важнее уведомления
        logger.exception(
            "tsk-1338: не удалось уведомить маркетологов о регистрации user_id=%s via=%s",
            user_id, via,
        )
        return 0
