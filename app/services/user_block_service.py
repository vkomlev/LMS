"""tsk-432: блокировка учётной записи — закрыть вход, не трогая данные.

Блокировка и слияние — разные состояния, и путать их нельзя. Слитая учётка
(`is_active=false`) исчезает из списков: человека там больше нет, он «переехал»
в другую запись. Заблокированный человек есть — его работы, попытки и история
нужны преподавателю, — просто вход закрыт.

Само по себе поле ничего не закрывает: отказ живёт в `get_current_user` (он
грузит пользователя на каждом запросе, поэтому блокировка действует сразу) и на
путях авторизации, чтобы человек получил внятный отказ при входе, а не после.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.models.association_tables import t_user_roles
from app.models.roles import Roles
from app.services.auth import session_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)


async def _get_user(db: AsyncSession, user_id: int) -> Users:
    user = (await db.execute(select(Users).where(Users.id == user_id))).scalar_one_or_none()
    if user is None:
        raise DomainError(f"Пользователь id={user_id} не найден", status_code=404)
    return user


async def _count_other_active_admins(db: AsyncSession, exclude_user_id: int) -> int:
    """Сколько ещё администраторов могут войти, кроме этого."""
    stmt = (
        select(func.count(func.distinct(Users.id)))
        .select_from(Users)
        .join(t_user_roles, t_user_roles.c.user_id == Users.id)
        .join(Roles, Roles.id == t_user_roles.c.role_id)
        .where(
            Roles.name == "admin",
            Users.id != exclude_user_id,
            Users.is_active.is_(True),
            Users.blocked_at.is_(None),
        )
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def block_user(
    db: AsyncSession,
    *,
    user_id: int,
    actor_id: Optional[int],
    reason: Optional[str],
) -> Users:
    """Закрыть вход и оборвать открытые сеансы.

    Без отзыва сеансов блокировка была бы отложенной: человек продолжал бы
    работать в уже открытой вкладке, пока не протухнет сессия.
    """
    user = await _get_user(db, user_id)

    if actor_id is not None and actor_id == user_id:
        raise DomainError(
            "Нельзя заблокировать самого себя — вы потеряете доступ к кабинету",
            status_code=409,
        )
    if not await _count_other_active_admins(db, exclude_user_id=user_id):
        # Проверяем ДО правки: иначе школа осталась бы без единого админа, и
        # разблокировать было бы некому — только правкой в базе.
        is_admin = (await db.execute(
            select(func.count())
            .select_from(t_user_roles)
            .join(Roles, Roles.id == t_user_roles.c.role_id)
            .where(t_user_roles.c.user_id == user_id, Roles.name == "admin")
        )).scalar()
        if is_admin:
            raise DomainError(
                "Это последний администратор со входом — заблокировать его "
                "некому будет разблокировать. Сначала назначьте другого.",
                status_code=409,
            )

    if user.blocked_at is not None:
        return user  # уже закрыт, повторный вызов ничего не меняет

    user.blocked_at = datetime.now(timezone.utc)
    user.blocked_reason = (reason or "").strip() or None
    user.blocked_by_user_id = actor_id
    await session_service.revoke_all_sessions(db, user_id)
    await db.commit()
    await db.refresh(user)
    logger.info(
        "tsk-432 вход закрыт: user_id=%s кем=%s причина=%r", user_id, actor_id, user.blocked_reason
    )
    return user


async def unblock_user(db: AsyncSession, *, user_id: int, actor_id: Optional[int]) -> Users:
    """Открыть вход обратно. Сеансы не восстанавливаются — человек входит заново."""
    user = await _get_user(db, user_id)
    if user.blocked_at is None:
        return user

    user.blocked_at = None
    user.blocked_reason = None
    user.blocked_by_user_id = None
    await db.commit()
    await db.refresh(user)
    logger.info("tsk-432 вход открыт: user_id=%s кем=%s", user_id, actor_id)
    return user


async def assert_not_blocked(db: AsyncSession, user_id: int) -> None:
    """Отказать заблокированному на входе.

    Вызывается на путях авторизации ДО создания сеанса: человек должен получить
    отказ при попытке войти, а не «войти» и упереться в ошибку на первом экране.
    """
    row = (await db.execute(
        select(Users.blocked_at).where(Users.id == user_id)
    )).first()
    if row is not None and row.blocked_at is not None:
        raise DomainError(BLOCKED_MESSAGE, status_code=403)


BLOCKED_MESSAGE = "Доступ к аккаунту закрыт. Обратитесь к администратору школы."
