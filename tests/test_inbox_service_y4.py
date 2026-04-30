"""Unit-тесты InboxService (Phase Y-4) — create / list / unread_count / mark_read."""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import inbox_service


async def _create_user(db) -> int:
    u = Users(email=None, password_hash=None, full_name=f"inbox-test-{random.randint(10**8, 10**10)}", tg_id=None)
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _cleanup(db, user_id: int) -> None:
    await db.execute(text("DELETE FROM notifications WHERE user_id=:u"), {"u": user_id})
    await db.commit()


@pytest.mark.asyncio
async def test_create_for_user_persists_inbox_record(db):
    uid = await _create_user(db)
    try:
        rec = await inbox_service.create_for_user(
            db,
            user_id=uid,
            kind="sa_com_graded",
            title="Преподаватель оценил",
            content="Балл: 7/10",
            payload={"task_id": 42, "score": 7, "max_score": 10},
            created_by=None,
        )
        await db.commit()
        assert rec.id is not None
        assert rec.user_id == uid
        assert rec.kind == "sa_com_graded"
        assert rec.read_at is None
        assert rec.payload["task_id"] == 42
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_unread_count_only_counts_unread(db):
    uid = await _create_user(db)
    try:
        # 3 непрочитанных
        for i in range(3):
            await inbox_service.create_for_user(
                db, user_id=uid, kind="sa_com_graded",
                title="t", content=f"c{i}", payload={}, created_by=None,
            )
        await db.commit()
        # 1 пометить прочитанным
        first = (
            await db.execute(
                text("SELECT id FROM notifications WHERE user_id=:u ORDER BY id LIMIT 1"),
                {"u": uid},
            )
        ).scalar()
        await db.execute(
            text("UPDATE notifications SET read_at=now() WHERE id=:id"),
            {"id": first},
        )
        await db.commit()

        count = await inbox_service.unread_count(db, uid)
        assert count == 2
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_list_for_user_pagination_and_unread_only(db):
    uid = await _create_user(db)
    try:
        for i in range(5):
            await inbox_service.create_for_user(
                db, user_id=uid, kind="sa_com_graded",
                title=f"t{i}", content=f"c{i}", payload={}, created_by=None,
            )
        await db.commit()
        # mark first as read
        first_id = (
            await db.execute(
                text("SELECT id FROM notifications WHERE user_id=:u ORDER BY id LIMIT 1"),
                {"u": uid},
            )
        ).scalar()
        await db.execute(
            text("UPDATE notifications SET read_at=now() WHERE id=:id"),
            {"id": first_id},
        )
        await db.commit()

        # all
        items_all = await inbox_service.list_for_user(
            db, user_id=uid, limit=10, offset=0, unread_only=False
        )
        assert len(items_all) == 5

        # unread only
        items_unread = await inbox_service.list_for_user(
            db, user_id=uid, limit=10, offset=0, unread_only=True
        )
        assert len(items_unread) == 4

        # pagination
        page1 = await inbox_service.list_for_user(
            db, user_id=uid, limit=2, offset=0, unread_only=False
        )
        assert len(page1) == 2
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_mark_read_atomic_idempotent(db):
    uid = await _create_user(db)
    try:
        rec = await inbox_service.create_for_user(
            db, user_id=uid, kind="sa_com_graded",
            title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()

        # First mark
        ts = await inbox_service.mark_read(db, rec.id, uid)
        assert ts is not None
        await db.commit()

        # Second mark — idempotent (already read → None)
        ts2 = await inbox_service.mark_read(db, rec.id, uid)
        assert ts2 is None
    finally:
        await _cleanup(db, uid)


@pytest.mark.asyncio
async def test_mark_read_idor_protection(db):
    """mark_read для чужой записи возвращает None (UPDATE rowcount=0)."""
    owner_id = await _create_user(db)
    other_id = await _create_user(db)
    try:
        rec = await inbox_service.create_for_user(
            db, user_id=owner_id, kind="sa_com_graded",
            title="t", content="c", payload={}, created_by=None,
        )
        await db.commit()

        # other_id пытается пометить чужую запись
        ts = await inbox_service.mark_read(db, rec.id, other_id)
        assert ts is None

        # get_status подтверждает что owner — другой user
        status_pair = await inbox_service.get_status(db, rec.id)
        assert status_pair is not None
        assert status_pair[0] == owner_id
        assert status_pair[1] is None  # ещё не прочитано
    finally:
        await _cleanup(db, owner_id)
        await _cleanup(db, other_id)


@pytest.mark.asyncio
async def test_get_status_nonexistent_returns_none(db):
    result = await inbox_service.get_status(db, 999999999)
    assert result is None
