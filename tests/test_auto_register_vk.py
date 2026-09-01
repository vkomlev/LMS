"""Auto-create user в /auth/vk/callback + 409 identity_conflict (Phase Y-1.5, ADR-0021).

Юнит-тесты сервисного слоя — обходим VK OAuth exchange (intеграционный путь).
"""
import os
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth.vk_oauth_service import (
    IdentityConflictError,
    get_or_create_user_by_vk,
)
from app.services.fernet_service import decrypt_token


def _new_vk_id() -> str:
    return str(random.SystemRandom().randint(10**8, 10**10))


@pytest.mark.asyncio
async def test_first_time_vk_with_email_creates_user(db):
    """VK с email scope → user.email + identity_link kind='vk' и kind='email'."""
    settings = Settings()
    vk_id = _new_vk_id()
    email = f"vkuser-{os.urandom(4).hex()}@example.com"
    expires = datetime.now(timezone.utc) + timedelta(seconds=3600)

    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=email, full_name="VK User",
        access_token="acc_test_xyz", refresh_token="ref_test_xyz",
        expires_at=expires, settings=settings, ip="127.0.0.1", user_agent="test",
    )
    await db.commit()

    assert created is True
    assert user.email == email
    assert user.full_name == "VK User"

    vk_link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalar_one_or_none()
    assert vk_link is not None and vk_link.user_id == user.id

    email_link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "email", IdentityLink.value == email)
    )).scalar_one_or_none()
    assert email_link is not None and email_link.user_id == user.id


@pytest.mark.asyncio
async def test_first_time_vk_no_email_creates_user_email_null(db):
    """VK без email → user.email=NULL, только identity_link kind='vk'."""
    settings = Settings()
    vk_id = _new_vk_id()

    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name="No-Email User",
        access_token="acc_xyz", refresh_token=None,
        expires_at=None, settings=settings, ip=None, user_agent=None,
    )
    await db.commit()

    assert created is True
    assert user.email is None
    assert user.full_name == "No-Email User"


@pytest.mark.asyncio
async def test_vk_email_match_links_to_existing_account(db):
    """tsk-755: совпадение почты — привязка к своему аккаунту, а не тупик.

    Было (ADR-0021 §2): 409, «войдите по почте, потом привяжите ВК».
    Стало (ADR-0054): ВК подтверждает почту, значит человек — владелец адреса,
    и его ВК привязывается к аккаунту с этим адресом.
    """
    settings = Settings()
    rand = os.urandom(4).hex()
    email = f"conflict-{rand}@example.com"

    existing = Users(email=email, password_hash=None, full_name="Pre-existing")
    db.add(existing)
    await db.flush()
    db.add(IdentityLink(user_id=existing.id, kind="email", value=email))
    await db.flush()
    await db.commit()

    new_vk_id = _new_vk_id()
    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=new_vk_id, email=email, full_name="Тот же человек",
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
    )
    await db.commit()

    assert created is False
    assert user.id == existing.id
    assert user.full_name == "Pre-existing", "имя из ВК не перетирает профиль"
    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == new_vk_id)
    )).scalar_one()
    assert link.user_id == existing.id


@pytest.mark.asyncio
async def test_vk_access_token_encrypted(db):
    """access_token и refresh_token сохранены как Fernet-encrypted bytea (decrypt back)."""
    settings = Settings()
    vk_id = _new_vk_id()

    user, _ = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name=None,
        access_token="raw_access_xyz", refresh_token="raw_refresh_xyz",
        expires_at=None, settings=settings, ip=None, user_agent=None,
    )
    await db.commit()

    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalar_one()

    assert link.vk_access_token_enc is not None
    assert isinstance(link.vk_access_token_enc, (bytes, bytearray))
    assert decrypt_token(link.vk_access_token_enc, settings) == "raw_access_xyz"
    assert decrypt_token(link.vk_refresh_token_enc, settings) == "raw_refresh_xyz"


@pytest.mark.asyncio
async def test_orphan_email_links_to_card_and_repairs_identity(db):
    """tsk-755: почта только в карточке (`users.email`) — тоже привязка.

    S2 regression (handoff 2026-04-28 §2) остаётся закрытой: INSERT нового
    пользователя с этим адресом упал бы на partial unique index, 500 быть не
    должно. Раньше здесь стоял 409; теперь ВК привязывается к самой карточке,
    а недостающая email-identity достраивается — иначе следующий вход по письму
    завёл бы человеку второй, пустой аккаунт рядом с настоящим.
    """
    settings = Settings()
    rand = os.urandom(4).hex()
    email = f"orphan-{rand}@example.com"

    # users.email есть, identity_link kind='email' нет.
    orphan_user = Users(email=email, password_hash=None, full_name="Orphan")
    db.add(orphan_user)
    await db.flush()
    await db.commit()

    new_vk_id = _new_vk_id()
    user, created = await get_or_create_user_by_vk(
        db, vk_user_id=new_vk_id, email=email, full_name="Из ВК",
        access_token="acc", refresh_token=None, expires_at=None,
        settings=settings, ip=None, user_agent=None,
    )
    await db.commit()

    assert created is False
    assert user.id == orphan_user.id
    kinds = {row[0] for row in (await db.execute(
        select(IdentityLink.kind).where(IdentityLink.user_id == orphan_user.id)
    )).all()}
    assert kinds == {"vk", "email"}, "email-identity достроена, вход по письму ведёт сюда же"


@pytest.mark.asyncio
async def test_existing_vk_login_rotates_tokens(db):
    """Existing VK identity: повторный login обновляет access/refresh tokens."""
    settings = Settings()
    vk_id = _new_vk_id()
    expires_1 = datetime.now(timezone.utc) + timedelta(seconds=3600)

    user1, c1 = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name="V1",
        access_token="acc_1", refresh_token="ref_1",
        expires_at=expires_1, settings=settings, ip=None, user_agent=None,
    )
    await db.commit()
    assert c1 is True

    expires_2 = datetime.now(timezone.utc) + timedelta(seconds=7200)
    user2, c2 = await get_or_create_user_by_vk(
        db, vk_user_id=vk_id, email=None, full_name="V1-updated",
        access_token="acc_2", refresh_token="ref_2",
        expires_at=expires_2, settings=settings, ip=None, user_agent=None,
    )
    await db.commit()

    assert c2 is False
    assert user1.id == user2.id

    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "vk", IdentityLink.value == vk_id)
    )).scalar_one()
    assert decrypt_token(link.vk_access_token_enc, settings) == "acc_2"
    assert decrypt_token(link.vk_refresh_token_enc, settings) == "ref_2"
