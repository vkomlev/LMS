"""tsk-172: ensure_student_access_request — заявка на student для role-holder.

Вариант A: заявку 'not_ready' создаём только если у пользователя уже есть роль,
но нет student и нет существующей заявки. Тесты не коммитят — фикстура `db`
откатывает транзакцию, отдельная чистка не нужна.
"""
import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth.role_assign_service import (
    STUDENT_ROLE_ID,
    ensure_student_access_request,
)


async def _make_user(db, *, roles: list[int]) -> int:
    """Создать пользователя с заданными ролями (без commit)."""
    u = Users(full_name="TSK172 test")
    db.add(u)
    await db.flush()
    for rid in roles:
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": u.id, "r": rid},
        )
    await db.flush()
    return u.id


async def _count_student_requests(db, user_id: int) -> int:
    return (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM access_requests "
                "WHERE user_id = :u AND role_id = :r"
            ),
            {"u": user_id, "r": STUDENT_ROLE_ID},
        )
    ).scalar()


@pytest.mark.asyncio
async def test_role_holder_without_student_gets_request(db):
    """teacher(3) без student → создаётся заявка not_ready."""
    uid = await _make_user(db, roles=[3])  # teacher

    created = await ensure_student_access_request(db, uid, channel="test")
    assert created is True

    row = (
        await db.execute(
            text(
                "SELECT flag::text FROM access_requests "
                "WHERE user_id = :u AND role_id = :r"
            ),
            {"u": uid, "r": STUDENT_ROLE_ID},
        )
    ).first()
    assert row is not None and row[0] == "not_ready"


@pytest.mark.asyncio
async def test_idempotent_no_duplicate_request(db):
    """Повторный вызов не плодит вторую заявку."""
    uid = await _make_user(db, roles=[3])

    assert await ensure_student_access_request(db, uid, channel="test") is True
    assert await ensure_student_access_request(db, uid, channel="test") is False
    assert await _count_student_requests(db, uid) == 1


@pytest.mark.asyncio
async def test_user_without_roles_is_noop(db):
    """Нет ролей (pure student) → no-op, заявку не создаём (авто-назначение роли)."""
    uid = await _make_user(db, roles=[])

    assert await ensure_student_access_request(db, uid, channel="test") is False
    assert await _count_student_requests(db, uid) == 0


@pytest.mark.asyncio
async def test_user_with_student_role_is_noop(db):
    """Уже student → no-op."""
    uid = await _make_user(db, roles=[STUDENT_ROLE_ID])

    assert await ensure_student_access_request(db, uid, channel="test") is False
    assert await _count_student_requests(db, uid) == 0


@pytest.mark.asyncio
async def test_existing_request_any_status_is_noop(db):
    """Заявка уже есть (например, rejected) → не воскрешаем."""
    uid = await _make_user(db, roles=[3])
    await db.execute(
        text(
            "INSERT INTO access_requests (user_id, role_id, flag) "
            "VALUES (:u, :r, 'rejected')"
        ),
        {"u": uid, "r": STUDENT_ROLE_ID},
    )
    await db.flush()

    assert await ensure_student_access_request(db, uid, channel="test") is False
    assert await _count_student_requests(db, uid) == 1
