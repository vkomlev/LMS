"""Сервис атрибуции guest_attempt → user (Phase Y-1 + Y-5).

Phase Y-1: `attribute_guest_session` вызывается в-rege transaction
из `magic_link_service`/`tg_init_service`/`vk_oauth_service` при
auto-registration — атрибутирует guest_attempt в той же tx.

Phase Y-5: `attribute_guest_post_login` — отдельный entry-point для
случаев, когда юзер вошёл existing identity (не registration), а
frontend сохранил `guest_session_id` cookie. Использует SAVEPOINT
(`db.begin_nested`) для безопасной обработки кросс-юзер conflict.

См. tech-spec Y-5 §6.2.4 + §13 G10.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guest_attempt import GuestAttempt
from app.models.guest_session import GuestSession
from app.models.users import Users

logger = logging.getLogger(__name__)

#: tsk-1407: квиз привязывают к кабинету сразу после регистрации (claim) —
#: метку сессии считаем источником регистрации, только если учётке не больше суток.
_SIGNUP_WINDOW = timedelta(days=1)


async def _copy_signup_attribution(
    db: AsyncSession,
    guest_session_id: Union[str, UUID],
    user_id: int,
    attribution: Optional[dict] = None,
) -> None:
    """Скопировать метки гостевой сессии в `users.signup_attribution` (tsk-1407).

    Пишет только в пустое поле: первое касание не перезаписывается.
    """
    if attribution is None:
        attribution = (
            await db.execute(
                select(GuestSession.attribution).where(GuestSession.id == guest_session_id)
            )
        ).scalar_one_or_none()
    if not attribution:
        return
    result = await db.execute(
        update(Users)
        .where(Users.id == user_id, Users.signup_attribution.is_(None))
        .values(signup_attribution={**attribution, "guest_session_id": str(guest_session_id)})
    )
    if result.rowcount:
        logger.info("signup_attribution: user_id=%d session=%s", user_id, guest_session_id)


class GuestAttributionConflictError(Exception):
    """Guest_session уже атрибутирован на другого пользователя.

    Y-5 §6.2.4: возвращается 409 на API-уровне; защита от перехвата
    cookie с UUID гостевой сессии другим пользователем.
    """


@dataclass(slots=True)
class AttributionResult:
    """Итог attribute_guest_post_login."""

    found: bool
    already_attributed: bool
    attributed_count: int


async def attribute_guest_session(
    db: AsyncSession,
    guest_session_id: str,
    user_id: int,
    *,
    is_new_user: bool = False,
) -> int:
    """
    Phase Y-1: привязать guest_session и все её попытки к пользователю.

    Возвращает количество обновлённых попыток.

    Вызывается ИЗ внутри transaction registration handler'а (magic-link
    verify / tg init / vk callback). В Y-5 НЕ применяется савепоинт-
    pattern — outer tx уже даёт atomicity registration+attribution.

    :param is_new_user: пользователь только что создан — метки сессии
        становятся источником регистрации (tsk-1407).
    """
    now = datetime.now(timezone.utc)

    await db.execute(
        update(GuestSession)
        .where(GuestSession.id == guest_session_id)
        .values(attributed_user_id=user_id)
    )

    result = await db.execute(
        update(GuestAttempt)
        .where(
            GuestAttempt.guest_session_id == guest_session_id,
            GuestAttempt.attributed_user_id.is_(None),
        )
        .values(attributed_user_id=user_id, attributed_at=now)
    )
    if is_new_user:
        await _copy_signup_attribution(db, guest_session_id, user_id)
    await db.flush()
    count: int = result.rowcount
    logger.info(
        "attribute_guest_session: session=%s user_id=%d attempts=%d",
        guest_session_id,
        user_id,
        count,
    )
    return count


async def attribute_guest_post_login(
    db: AsyncSession,
    user_id: int,
    guest_session_id: UUID,
) -> AttributionResult:
    """
    Phase Y-5: атрибуция guest_session к user после login (не registration).

    Idempotent: повторный вызов с теми же (user_id, guest_session_id) →
    `already_attributed=True, attributed_count=0`.

    Concurrency: SAVEPOINT (`db.begin_nested`) изолирует SELECT FOR UPDATE +
    UPDATE — race двух одновременных запросов сериализуется; кросс-юзер
    conflict даст GuestAttributionConflictError, при этом outer tx (если
    есть) НЕ откатывается (LMS ERRORS #3 — ловушка с db.rollback()).

    Raises:
        GuestAttributionConflictError: если guest_session уже принадлежит
            другому пользователю.
    """
    async with db.begin_nested():
        gs_row = await db.execute(
            select(GuestSession)
            .where(GuestSession.id == guest_session_id)
            .with_for_update()
        )
        gs = gs_row.scalar_one_or_none()
        if gs is None:
            return AttributionResult(found=False, already_attributed=False, attributed_count=0)

        if gs.attributed_user_id is not None:
            if gs.attributed_user_id != user_id:
                raise GuestAttributionConflictError(
                    f"guest_session={guest_session_id} уже атрибутирован на user_id={gs.attributed_user_id}"
                )
            # Идемпотент: уже принадлежит этому юзеру
            return AttributionResult(found=True, already_attributed=True, attributed_count=0)

        now = datetime.now(timezone.utc)
        gs.attributed_user_id = user_id
        result = await db.execute(
            update(GuestAttempt)
            .where(
                GuestAttempt.guest_session_id == guest_session_id,
                GuestAttempt.attributed_user_id.is_(None),
            )
            .values(attributed_user_id=user_id, attributed_at=now)
        )
        user = await db.get(Users, user_id)
        if user is not None and user.created_at >= now - _SIGNUP_WINDOW:
            await _copy_signup_attribution(db, guest_session_id, user_id, gs.attribution)
        await db.flush()

    count = int(result.rowcount or 0)
    logger.info(
        "attribute_guest_post_login: session=%s user_id=%d attempts=%d",
        guest_session_id,
        user_id,
        count,
    )
    return AttributionResult(found=True, already_attributed=False, attributed_count=count)
