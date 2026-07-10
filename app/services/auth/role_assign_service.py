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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_requests import AccessRequests
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


async def ensure_student_access_request(
    db: AsyncSession,
    user_id: int,
    *,
    channel: str,
) -> bool:
    """Создать заявку (access_request) на роль student для role-holder без student.

    Мотивация (tsk-172): пользователь, у которого УЖЕ есть роль (teacher/
    methodist/admin), при входе в SPW не получает student автоматически —
    `ensure_student_role` назначает роль только при ПОЛНОМ отсутствии ролей.
    Чтобы совмещение ролей (teacher+student) можно было выдать через штатный
    поток одобрения, формируем заявку `not_ready`, которая попадает в очередь
    админ-бота.

    Правила (вариант A, tsk-172):
    - нет НИ ОДНОЙ роли → no-op (pure student: авто-назначение делает
      `ensure_student_role`, без approval);
    - уже есть роль student → no-op;
    - уже есть заявка на student в ЛЮБОМ статусе → no-op (не дублируем и не
      воскрешаем после rejected/completed);
    - иначе INSERT access_request(role_id=student, flag='not_ready').

    Идемпотентно. Caller отвечает за commit. Soft-fail: caller оборачивает в
    try/except, чтобы сбой создания заявки не блокировал вход. Возвращает True,
    если заявка создана сейчас.
    """
    roles = (
        await db.execute(
            text("SELECT role_id FROM user_roles WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).scalars().all()
    if not roles:
        return False
    if STUDENT_ROLE_ID in roles:
        return False

    existing = await db.execute(
        text(
            "SELECT 1 FROM access_requests "
            "WHERE user_id = :uid AND role_id = :rid LIMIT 1"
        ),
        {"uid": user_id, "rid": STUDENT_ROLE_ID},
    )
    if existing.fetchone() is not None:
        return False

    # INSERT в SAVEPOINT (Y-1.5 lesson): сбой вставки откатывает только nested
    # транзакцию и НЕ отравляет внешнюю — иначе последующий db.commit() в
    # auth-эндпоинте упал бы и сломал вход, несмотря на soft-fail в caller.
    try:
        async with db.begin_nested():
            db.add(AccessRequests(user_id=user_id, role_id=STUDENT_ROLE_ID))
            await db.flush()
    except IntegrityError:
        # Гонка параллельного входа/иное нарушение — заявку не создаём, no-op.
        return False

    logger.info(
        "tsk-172: student access_request created for user_id=%s (channel=%s)",
        user_id, channel,
    )
    return True
