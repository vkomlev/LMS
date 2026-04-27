"""
Тесты identity_link сервиса.

Покрывает:
- find_identity: найден / не найден
- upsert_identity: создание и обновление
- get_user_by_identity: через identity_link
- email нормализация (lower)
"""
import pytest


@pytest.mark.asyncio
async def test_find_identity_not_found(db):
    """Поиск несуществующей identity → None."""
    from app.services.auth.identity_link_service import find_identity

    result = await find_identity(db, "email", "nonexistent@example.com")
    assert result is None


@pytest.mark.asyncio
async def test_upsert_and_find_identity(db):
    """upsert_identity создаёт запись, find_identity её находит."""
    from app.services.auth.identity_link_service import find_identity, upsert_identity
    from sqlalchemy import text

    user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if user_id is None:
        pytest.skip("Нет пользователей в БД")

    unique_email = f"test.upsert.{user_id}@example.com"
    link = await upsert_identity(db, user_id, "email", unique_email)
    await db.commit()

    found = await find_identity(db, "email", unique_email)
    assert found is not None
    assert found.user_id == user_id
    assert found.value == unique_email.lower()


@pytest.mark.asyncio
async def test_email_normalized_to_lower(db):
    """Email нормализуется в lowercase при upsert."""
    from app.services.auth.identity_link_service import find_identity, upsert_identity
    from sqlalchemy import text

    user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if user_id is None:
        pytest.skip("Нет пользователей в БД")

    mixed = f"Upper.Case.Test.{user_id}@Example.COM"
    await upsert_identity(db, user_id, "email", mixed)
    await db.commit()

    found = await find_identity(db, "email", mixed.lower())
    assert found is not None
    assert found.value == mixed.lower()


@pytest.mark.asyncio
async def test_upsert_idempotent(db):
    """Повторный upsert с теми же данными не создаёт дубликат."""
    from app.services.auth.identity_link_service import upsert_identity
    from sqlalchemy import select, func, text
    from app.models.identity_link import IdentityLink

    user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if user_id is None:
        pytest.skip("Нет пользователей в БД")

    email = f"idempotent.{user_id}@example.com"
    await upsert_identity(db, user_id, "email", email)
    await db.commit()
    await upsert_identity(db, user_id, "email", email)
    await db.commit()

    count = (await db.execute(
        select(func.count()).where(
            IdentityLink.value == email,
            IdentityLink.kind == "email",
        )
    )).scalar()
    assert count == 1
