"""Integration-тесты identity_link_service.link_existing_user (Phase Y-3).

Покрывает:
- happy: email/tg/vk привязка к existing user — INSERT identity_link
- idempotent: повторная попытка с тем же (kind, value) для того же user → UPDATE last_used_at
- 409 conflict: identity занята другим user → IdentityConflictError
- 409 orphan: email уже в users.email без identity_link → IdentityConflictError
- VK Fernet: encrypted token поля сохраняются в identity_link
- TG sync: link_existing_user(kind='tg') обновляет users.tg_id
"""
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.exceptions import IdentityConflictError


def _rand_email() -> str:
    return f"y3test_{random.randint(10**8, 10**10)}@example.com"


def _rand_tg() -> str:
    return str(random.randint(10**12, 10**14))


def _rand_vk() -> str:
    return str(random.randint(10**8, 10**10))


async def _insert_user(db, *, email: str | None = None, tg_id: int | None = None) -> int:
    """Создать user без auto-create логики (минимально для теста)."""
    user = Users(email=email, password_hash=None, full_name="Y3-test", tg_id=tg_id)
    db.add(user)
    await db.flush()
    return user.id


async def _cleanup(db, user_id: int) -> None:
    """Cleanup без DELETE FROM users (см. test_me_endpoints_y3._cleanup)."""
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── happy path ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_email_happy(db):
    user_id = await _insert_user(db)
    email = _rand_email()
    try:
        link = await identity_link_service.link_existing_user(db, user_id, "email", email)
        await db.commit()
        assert link.user_id == user_id
        assert link.kind == "email"
        assert link.value == email.lower()
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_tg_happy_syncs_users_tg_id(db):
    user_id = await _insert_user(db)
    tg = _rand_tg()
    try:
        link = await identity_link_service.link_existing_user(db, user_id, "tg", tg)
        await db.commit()
        assert link.user_id == user_id
        # Sync users.tg_id
        synced = (
            await db.execute(text("SELECT tg_id FROM users WHERE id=:u"), {"u": user_id})
        ).scalar()
        assert synced == int(tg)
    finally:
        await _cleanup(db, user_id)


@pytest.mark.asyncio
async def test_link_vk_with_fernet_tokens(db):
    user_id = await _insert_user(db)
    vk = _rand_vk()
    enc_access = b"\x00\x01encrypted-access\x02"
    enc_refresh = b"\x00\x01encrypted-refresh\x02"
    expires = datetime.now(timezone.utc)
    try:
        link = await identity_link_service.link_existing_user(
            db, user_id, "vk", vk,
            vk_access_token_enc=enc_access,
            vk_refresh_token_enc=enc_refresh,
            vk_token_expires_at=expires,
        )
        await db.commit()
        assert link.vk_access_token_enc == enc_access
        assert link.vk_refresh_token_enc == enc_refresh
    finally:
        await _cleanup(db, user_id)


# ── idempotent ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_idempotent_for_same_user(db):
    user_id = await _insert_user(db)
    email = _rand_email()
    try:
        link1 = await identity_link_service.link_existing_user(db, user_id, "email", email)
        await db.commit()
        link2 = await identity_link_service.link_existing_user(db, user_id, "email", email)
        await db.commit()
        # Тот же ID, last_used_at обновлён
        assert link1.id == link2.id
        assert link2.last_used_at is not None
    finally:
        await _cleanup(db, user_id)


# ── 409 conflict ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_email_conflict_other_user(db):
    user_a = await _insert_user(db)
    user_b = await _insert_user(db)
    email = _rand_email()
    try:
        # User A привязал email
        await identity_link_service.link_existing_user(db, user_a, "email", email)
        await db.commit()
        # User B пытается привязать тот же email
        with pytest.raises(IdentityConflictError) as exc:
            await identity_link_service.link_existing_user(db, user_b, "email", email)
        assert exc.value.conflict_kind == "email_already_linked"
        assert "email" in exc.value.existing_kinds
    finally:
        await _cleanup(db, user_a)
        await _cleanup(db, user_b)


@pytest.mark.asyncio
async def test_link_tg_conflict_other_user(db):
    user_a = await _insert_user(db)
    user_b = await _insert_user(db)
    tg = _rand_tg()
    try:
        await identity_link_service.link_existing_user(db, user_a, "tg", tg)
        await db.commit()
        with pytest.raises(IdentityConflictError) as exc:
            await identity_link_service.link_existing_user(db, user_b, "tg", tg)
        assert exc.value.conflict_kind == "tg_already_linked"
    finally:
        await _cleanup(db, user_a)
        await _cleanup(db, user_b)


@pytest.mark.asyncio
async def test_link_vk_conflict_other_user(db):
    user_a = await _insert_user(db)
    user_b = await _insert_user(db)
    vk = _rand_vk()
    try:
        await identity_link_service.link_existing_user(db, user_a, "vk", vk)
        await db.commit()
        with pytest.raises(IdentityConflictError) as exc:
            await identity_link_service.link_existing_user(db, user_b, "vk", vk)
        assert exc.value.conflict_kind == "vk_already_linked"
    finally:
        await _cleanup(db, user_a)
        await _cleanup(db, user_b)


@pytest.mark.asyncio
async def test_link_email_orphan_user(db):
    """Y-1.5.1 защита: users.email exists без identity_link → 409."""
    email = _rand_email()
    # Orphan: user с email но без identity_link
    orphan_id = await _insert_user(db, email=email)
    other_id = await _insert_user(db)
    try:
        await db.commit()
        with pytest.raises(IdentityConflictError) as exc:
            await identity_link_service.link_existing_user(db, other_id, "email", email)
        assert exc.value.conflict_kind == "email_already_linked_to_orphan_user"
        assert exc.value.existing_kinds == []
    finally:
        await _cleanup(db, orphan_id)
        await _cleanup(db, other_id)
