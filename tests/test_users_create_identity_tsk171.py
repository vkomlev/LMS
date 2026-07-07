"""tsk-171: UsersService.create синхронно регистрирует identity_link.

Пользователь, созданный через сервис с email/tg_id, больше не «orphan» —
находится auth-флоу SPW по email и tg (иначе magic-link/VK отдают 409).
Тесты чистят за собой, т.к. create() коммитит транзакцию.
"""
import uuid

import pytest
from sqlalchemy import text


async def _cleanup(db, user_id: int) -> None:
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :uid"), {"uid": user_id})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_create_user_registers_email_and_tg_identity(db):
    """create с email+tg_id создаёт обе identity_link записи на нового user."""
    from app.services.users_service import UsersService
    from app.services.auth.identity_link_service import find_identity, get_user_by_identity

    suffix = uuid.uuid4().hex[:12]
    email = f"tsk171.{suffix}@example.com"
    tg_id = 900_000_000 + (int(suffix[:6], 16) % 90_000_000)

    svc = UsersService()
    user = None
    try:
        user = await svc.create(
            db, {"email": email, "full_name": "TSK171 Test", "tg_id": tg_id}
        )

        email_link = await find_identity(db, "email", email)
        assert email_link is not None, "email identity_link не создан"
        assert email_link.user_id == user.id

        tg_link = await find_identity(db, "tg", str(tg_id))
        assert tg_link is not None, "tg identity_link не создан"
        assert tg_link.user_id == user.id

        # Ключевое: пользователь находится auth-флоу по email (не orphan → не 409).
        found = await get_user_by_identity(db, "email", email)
        assert found is not None and found.id == user.id
    finally:
        if user is not None:
            await _cleanup(db, user.id)


@pytest.mark.asyncio
async def test_create_user_without_email_or_tg_has_no_identity(db):
    """Без email и tg_id identity_link не создаётся (нечего привязывать)."""
    from app.services.users_service import UsersService

    svc = UsersService()
    user = None
    try:
        user = await svc.create(
            db, {"email": None, "full_name": "TSK171 NoIdentity", "tg_id": None}
        )
        cnt = (
            await db.execute(
                text("SELECT COUNT(*) FROM identity_link WHERE user_id = :uid"),
                {"uid": user.id},
            )
        ).scalar()
        assert cnt == 0
    finally:
        if user is not None:
            await _cleanup(db, user.id)
