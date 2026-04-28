"""Auto-create user в /auth/tg/init (Phase Y-1.5, ADR-0021).

Юнит-тесты сервисного слоя — проверяют get_or_create_user_by_tg напрямую
(избегаем complexity HMAC-валидной initData).
"""
import os
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.models.audit_event import AuditEvent
from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth.tg_init_service import get_or_create_user_by_tg


def _new_tg_id() -> int:
    """Случайный 64-bit positive int — гарантированно не пересекается с реальными tg_id."""
    return random.SystemRandom().randint(10**12, 10**14)


@pytest.mark.asyncio
async def test_first_time_tg_creates_user_with_full_name(db):
    """first_name + last_name → full_name; users.tg_id = tg_user_id; identity_link есть."""
    tg_id = _new_tg_id()
    user, created = await get_or_create_user_by_tg(
        db, tg_id, full_name="Виктор Тестовый", ip="127.0.0.1", user_agent="test",
    )
    await db.commit()

    assert created is True
    assert user.tg_id == tg_id
    assert user.full_name == "Виктор Тестовый"
    assert user.email is None

    link = (await db.execute(
        select(IdentityLink).where(IdentityLink.kind == "tg", IdentityLink.value == str(tg_id))
    )).scalar_one_or_none()
    assert link is not None
    assert link.user_id == user.id


@pytest.mark.asyncio
async def test_anonymous_tg_falls_back_to_guest_name(db):
    """full_name=None → fallback 'Гость TG-{last4}'."""
    tg_id = _new_tg_id()
    user, created = await get_or_create_user_by_tg(
        db, tg_id, full_name=None, ip="127.0.0.1", user_agent="test",
    )
    await db.commit()
    assert created is True
    assert user.full_name == f"Гость TG-{str(tg_id)[-4:]}"


@pytest.mark.asyncio
async def test_users_tg_id_synchronized_on_create(db):
    """После auto-create через /tg/init — users.tg_id заполнен."""
    tg_id = _new_tg_id()
    user, _ = await get_or_create_user_by_tg(
        db, tg_id, full_name="Sync Test", ip=None, user_agent=None,
    )
    await db.commit()

    fetched_tg = (await db.execute(
        select(Users.tg_id).where(Users.id == user.id)
    )).scalar_one()
    assert fetched_tg == tg_id


@pytest.mark.asyncio
async def test_existing_tg_with_outdated_tg_id_resyncs(db):
    """Existing identity_link kind='tg', users.tg_id отсутствует/устарел → UPDATE при init."""
    tg_id = _new_tg_id()

    user_row = Users(email=None, password_hash=None, full_name="Stale", tg_id=None)
    db.add(user_row)
    await db.flush()
    db.add(IdentityLink(user_id=user_row.id, kind="tg", value=str(tg_id)))
    await db.flush()
    await db.commit()

    user, created = await get_or_create_user_by_tg(
        db, tg_id, full_name=None, ip=None, user_agent=None,
    )
    await db.commit()

    assert created is False
    assert user.tg_id == tg_id


@pytest.mark.asyncio
async def test_audit_event_recorded_for_tg_registration(db):
    """user.registered.via_tg_init записывается в audit_event."""
    tg_id = _new_tg_id()
    user, created = await get_or_create_user_by_tg(
        db, tg_id, full_name="Audit Test", ip="127.0.0.1", user_agent="ua/1.0",
    )
    await db.commit()
    assert created is True

    audit = (await db.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "user.registered.via_tg_init",
            AuditEvent.user_id == user.id,
        )
    )).scalar_one_or_none()
    assert audit is not None
    assert audit.details["identity_kind"] == "tg"
    assert audit.details["full_name_source"] == "init_data"
