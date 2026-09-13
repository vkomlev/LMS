"""Ручное создание ученика администратором (tsk-931).

Для ученика, у которого никогда не было ни ВК-аккаунта, ни рабочей почты —
обычный путь регистрации (auto-create на первом входе через email/ВК/TG,
ADR-0021) недоступен вовсе: заводить учётку не на чем. Единственная ручка,
которая пишет в `users` (`POST /users/`), закрыта служебным API-ключом ботов
и не создаёт `identity_link` — из кабинета администратора (cookie-сессия) она
недоступна и оставляет orphan-пользователя.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.users import Users
from app.services import audit_service
from app.services.auth import role_assign_service


async def create_student_manually(
    db: AsyncSession,
    *,
    created_by_user_id: int,
    note: Optional[str],
) -> Users:
    """Создать пустую карточку ученика вручную.

    `full_name` остаётся NULL НАМЕРЕННО: заполненное здесь ФИО прошло бы
    клиентский гейт `FullNameGate` (SPW, tsk-223), и welcome-форма ФИО не
    показалась бы ученику при первом реальном входе. `note` — свободная
    заметка оператора (например, имя со слов клиента до уточнения фамилии,
    как в tsk-931 у «Кирилл Ф.») — уходит только в audit, не в карточку.
    Роль `student` и тариф по умолчанию назначает `ensure_student_role` — тот
    же путь, что у auto-create при обычной регистрации.
    """
    user = Users(full_name=None, email=None, password_hash=None, tg_id=None)
    db.add(user)
    await db.flush()

    await role_assign_service.ensure_student_role(
        db, user.id, channel="admin_manual_create", origin="admin_manual_create",
    )
    await audit_service.log_event(
        db,
        audit_service.ADMIN_STUDENT_CREATED_MANUALLY,
        user_id=user.id,
        details={"created_by_user_id": created_by_user_id, "note": note},
    )

    await db.commit()
    await db.refresh(user)
    return user
