"""Двусторонняя sync users.tg_id ↔ identity_link kind='tg' (Phase Y-1.5, ADR-0021 §3)."""
import random

import pytest
from sqlalchemy import select

from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth import identity_link_service


def _new_tg_id() -> int:
    return random.SystemRandom().randint(10**12, 10**14)


@pytest.mark.asyncio
async def test_upsert_identity_link_tg_updates_users_tg_id(db):
    """upsert_identity kind='tg' → users.tg_id = int(value)."""
    user = Users(email=None, password_hash=None, full_name="No TG yet", tg_id=None)
    db.add(user)
    await db.flush()

    tg_id = _new_tg_id()
    await identity_link_service.upsert_identity(db, user.id, "tg", str(tg_id))
    await db.commit()

    fetched_tg = (await db.execute(
        select(Users.tg_id).where(Users.id == user.id)
    )).scalar_one()
    assert fetched_tg == tg_id


@pytest.mark.asyncio
async def test_upsert_identity_link_tg_corrects_outdated_users_tg_id(db):
    """Existing identity_link kind='tg', users.tg_id рассинхронизирован → upsert UPDATE."""
    user = Users(email=None, password_hash=None, full_name="Stale TG", tg_id=99999999)
    db.add(user)
    await db.flush()

    correct_tg = _new_tg_id()
    await identity_link_service.upsert_identity(db, user.id, "tg", str(correct_tg))
    await db.commit()

    fetched_tg = (await db.execute(
        select(Users.tg_id).where(Users.id == user.id)
    )).scalar_one()
    assert fetched_tg == correct_tg


@pytest.mark.asyncio
async def test_upsert_identity_link_email_does_not_touch_tg_id(db):
    """upsert kind='email' не меняет users.tg_id (sync только для kind='tg')."""
    initial_tg = _new_tg_id()
    user = Users(email=None, password_hash=None, full_name="Has TG", tg_id=initial_tg)
    db.add(user)
    await db.flush()

    await identity_link_service.upsert_identity(db, user.id, "email", "some@example.test")
    await db.commit()

    fetched_tg = (await db.execute(
        select(Users.tg_id).where(Users.id == user.id)
    )).scalar_one()
    assert fetched_tg == initial_tg
