"""Y-4 pre-S5: тесты helper'а ensure_student_role + auto-assign в auth-сервисах."""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth.role_assign_service import (
    STUDENT_ROLE_ID,
    ensure_student_role,
)


async def _create_user_no_role(db) -> int:
    u = Users(
        email=f"y4pre-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y4pre-test", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _cleanup(db, user_ids: list[int]):
    if not user_ids:
        return
    await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": user_ids})
    await db.commit()


@pytest.mark.asyncio
async def test_ensure_student_role_assigns_when_no_roles(db):
    """User без ролей → assign student → return True."""
    uid = await _create_user_no_role(db)
    try:
        assigned = await ensure_student_role(
            db, uid, channel="test_unit", origin="auto_registration"
        )
        await db.commit()
        assert assigned is True

        roles = (
            await db.execute(
                text(
                    "SELECT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
                    "WHERE ur.user_id=:u"
                ),
                {"u": uid},
            )
        ).fetchall()
        assert [r[0] for r in roles] == ["student"]
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_ensure_student_role_skips_user_with_existing_role(db):
    """User с уже существующей ролью (любой) → no-op → return False."""
    uid = await _create_user_no_role(db)
    try:
        # Назначить teacher (id=3) — имитация manually-assigned admin role
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, 3)"),
            {"u": uid},
        )
        await db.commit()

        assigned = await ensure_student_role(
            db, uid, channel="test_unit", origin="defensive_self_heal"
        )
        await db.commit()
        assert assigned is False

        roles = sorted(
            r[0]
            for r in (
                await db.execute(
                    text(
                        "SELECT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
                        "WHERE ur.user_id=:u"
                    ),
                    {"u": uid},
                )
            ).fetchall()
        )
        # Только teacher; student НЕ добавлен
        assert roles == ["teacher"]
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_ensure_student_role_idempotent_concurrent(db):
    """Повторный вызов после assign — return False (уже есть)."""
    uid = await _create_user_no_role(db)
    try:
        first = await ensure_student_role(
            db, uid, channel="test_unit", origin="auto_registration"
        )
        await db.commit()
        second = await ensure_student_role(
            db, uid, channel="test_unit", origin="auto_registration"
        )
        await db.commit()
        assert first is True
        assert second is False
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_ensure_student_role_audit_event_recorded(db):
    """Audit event записан при assign."""
    uid = await _create_user_no_role(db)
    try:
        await ensure_student_role(
            db, uid, channel="magic_link", origin="auto_registration"
        )
        await db.commit()
        cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type='student.role.auto_assigned' AND user_id=:u"
                ),
                {"u": uid},
            )
        ).scalar()
        assert cnt >= 1
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_ensure_student_role_self_heal_event_type(db):
    """origin='defensive_self_heal' → event_type AUTH_ROLE_MISSING_SELF_HEALED."""
    uid = await _create_user_no_role(db)
    try:
        await ensure_student_role(
            db, uid,
            channel="get_current_user_defensive",
            origin="defensive_self_heal",
        )
        await db.commit()
        cnt = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM audit_event "
                    "WHERE event_type='auth.role.missing_self_healed' AND user_id=:u"
                ),
                {"u": uid},
            )
        ).scalar()
        assert cnt >= 1
    finally:
        await _cleanup(db, [uid])


@pytest.mark.asyncio
async def test_student_role_id_is_4(db):
    """Sanity: STUDENT_ROLE_ID совпадает с реальным id в roles."""
    real = (
        await db.execute(text("SELECT id FROM roles WHERE name='student'"))
    ).scalar()
    assert real == STUDENT_ROLE_ID
