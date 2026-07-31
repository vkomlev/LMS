"""
Тесты identity_link сервиса.

Покрывает:
- find_identity: найден / не найден
- upsert_identity: создание и обновление
- email нормализация (lower)

tsk-490 — почему здесь заводится СВОЙ пользователь, а не берётся существующий.
Раньше все три теста начинались с `SELECT MIN(id) FROM users` и вешали ключ
входа на первого попавшегося РЕАЛЬНОГО человека, да ещё с `commit()` и без
уборки. Запущенные однажды по боевой базе (2026-04-27), они оставили на
аккаунте оператора (админ + методист + преподаватель) три постоянных
email-привязки — то есть три незапланированных способа войти в самый
привилегированный аккаунт школы. Одноразовый пользователь убирает саму
возможность: тесту всё равно, кто там первый в таблице.
"""
import random

import pytest
from sqlalchemy import func, select, text

from app.models.identity_link import IdentityLink
from app.models.users import Users


async def _throwaway_user(db) -> int:
    """Одноразовый пользователь под один тест."""
    suffix = random.randint(10**8, 10**10)
    user = Users(
        email=f"identity-link-{suffix}@example.com",
        password_hash=None,
        full_name=f"identity-link-{suffix}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await db.commit()
    return user.id


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

    user_id = await _throwaway_user(db)
    unique_email = f"test.upsert.{user_id}@example.com"
    await upsert_identity(db, user_id, "email", unique_email)
    await db.commit()

    found = await find_identity(db, "email", unique_email)
    assert found is not None
    assert found.user_id == user_id
    assert found.value == unique_email.lower()


@pytest.mark.asyncio
async def test_email_normalized_to_lower(db):
    """Email нормализуется в lowercase при upsert."""
    from app.services.auth.identity_link_service import find_identity, upsert_identity

    user_id = await _throwaway_user(db)
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

    user_id = await _throwaway_user(db)
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


@pytest.mark.asyncio
async def test_tests_do_not_touch_pre_existing_users(db):
    """Ни один тест этого файла не вешает привязку на чужую учётку.

    Прямая защита от повторения tsk-490: проверяем, что у первого по номеру
    пользователя базы не появилось привязок с нашими тестовыми шаблонами.
    """
    first_user_id = (await db.execute(text("SELECT MIN(id) FROM users"))).scalar()
    if first_user_id is None:
        pytest.skip("Нет пользователей в БД")

    leaked = (await db.execute(
        select(func.count()).where(
            IdentityLink.user_id == first_user_id,
            IdentityLink.kind == "email",
            IdentityLink.value.in_([
                f"test.upsert.{first_user_id}@example.com",
                f"upper.case.test.{first_user_id}@example.com",
                f"idempotent.{first_user_id}@example.com",
            ]),
        )
    )).scalar()
    assert leaked == 0, (
        "Тест повесил email-привязку на существующего пользователя — "
        "на боевой базе это лишний ключ входа (tsk-490)"
    )
