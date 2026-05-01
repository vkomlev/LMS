"""Y-4 pre-S5: auto-assign student-роли пользователю.

Используется в двух точках:
1. **Auth-сервисы** (`magic_link_service`, `tg_init_service`, `vk_oauth_service`)
   при auto-registration first-time visitor — в той же savepoint-транзакции
   с INSERT users + identity_link.
2. **`get_current_user` defensive self-heal** — если у legacy-юзера нет роли,
   при любом auth-запросе тихо назначаем `student` + audit (не блокируем
   запрос даже если запись падает).

Идемпотентно: SELECT существующих ролей → если пусто, INSERT student
(role_id=4) с `ON CONFLICT (user_id, role_id) DO NOTHING`. Если у user
уже любая роль (teacher / methodist / admin / student) — no-op без audit.

Schema reminder (verified MCP 2026-05-01):
- `user_roles(user_id INT, role_id INT)` composite PK + FK на users/roles
- роль 'student' имеет id=4 (см. INSERT INTO roles seed в early migrations)
- В `users` НЕТ колонки `is_service` (это runtime-атрибут CurrentUser);
  потому фильтрация по is_service выполняется на caller-side
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit_service

logger = logging.getLogger(__name__)

STUDENT_ROLE_ID = 4  # Соответствует roles.name='student'; verified MCP 2026-05-01


async def ensure_student_role(
    db: AsyncSession,
    user_id: int,
    *,
    channel: str,
    origin: str,
) -> bool:
    """Назначить роль `student` пользователю, если у него нет НИ ОДНОЙ роли.

    Args:
        db: Async SQLAlchemy session (caller отвечает за commit).
        user_id: ID пользователя в `users.id`.
        channel: Источник вызова — `magic_link` / `tg_init` / `vk_callback` /
                 `get_current_user_defensive` / `auth_test_session`.
        origin: Контекст события — `auto_registration` или
                `defensive_self_heal` или `test_session_issue`.

    Returns:
        True если роль была назначена сейчас (audit записан),
        False если у user уже была любая роль (no-op).

    Никогда не raises (кроме fatal DB ошибок) — caller рассчитывает на
    soft-fail.
    """
    # SELECT существующих ролей; первый row → user уже имеет роль.
    existing = await db.execute(
        text("SELECT 1 FROM user_roles WHERE user_id = :uid LIMIT 1"),
        {"uid": user_id},
    )
    if existing.fetchone() is not None:
        # У user уже есть роль (любая) — no silent overwrite.
        return False

    # INSERT student. ON CONFLICT защищает от race с runtime self-heal на
    # параллельной транзакции; PK (user_id, role_id) гарантирует UNIQUE.
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "VALUES (:uid, :rid) "
            "ON CONFLICT (user_id, role_id) DO NOTHING"
        ),
        {"uid": user_id, "rid": STUDENT_ROLE_ID},
    )

    # Audit: тип события зависит от происхождения.
    event_type = (
        audit_service.AUTH_ROLE_MISSING_SELF_HEALED
        if origin == "defensive_self_heal"
        else audit_service.STUDENT_ROLE_AUTO_ASSIGNED
    )
    try:
        await audit_service.log_event(
            db,
            event_type,
            user_id=user_id,
            details={"channel": channel, "origin": origin, "role": "student"},
        )
    except Exception:
        # Audit-сбой не должен валить main-transaction — лог + продолжить.
        logger.exception(
            "audit_event для %s upal на user_id=%s, channel=%s",
            event_type, user_id, channel,
        )

    logger.info(
        "Y-4 pre-S5: student role assigned to user_id=%s (channel=%s, origin=%s)",
        user_id, channel, origin,
    )
    return True
