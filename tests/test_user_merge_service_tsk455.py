"""tsk-455: авто-слияние дубля сразу при регистрации нового аккаунта.

Живой инцидент: "плавающий" ученик (заведён вручную по ФИО, без identity)
и его же второй, самостоятельно зарегистрированный аккаунт провисели
несведёнными полдня — `scripts/tsk442_auto_merge_duplicates.py` никто не
запускал вручную. `check_and_merge_duplicate_on_registration` переносит ту
же безопасную логику (`users_dedup_service.select_auto_merge_pairs`) на
момент регистрации — без UI-диалога "это вы?" (решение оператора из
tsk-442 остаётся в силе), просто без ручного триггера.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.identity_link import IdentityLink
from app.models.users import Users
from app.services.auth.tg_init_service import get_or_create_user_by_tg
from app.services.user_merge_service import check_and_merge_duplicate_on_registration


async def _create_floating(db, full_name: str) -> int:
    u = Users(email=None, password_hash=None, full_name=full_name, tg_id=None)
    db.add(u)
    await db.flush()
    return u.id


async def _create_registered(db, full_name: str, *, email: str | None = None) -> int:
    suffix = random.randint(10**8, 10**10)
    email = email or f"tsk455-{suffix}@example.com"
    u = Users(email=email, password_hash=None, full_name=full_name, tg_id=None)
    db.add(u)
    await db.flush()
    db.add(IdentityLink(user_id=u.id, kind="email", value=email))
    await db.commit()
    return u.id


@pytest.mark.asyncio
async def test_merges_floating_into_new_registered_account(db):
    floating_id = await _create_floating(db, "Илья Рвачев")
    registered_id = await _create_registered(db, "Илья Рвачёв")

    merged_source_id = await check_and_merge_duplicate_on_registration(
        db, new_user_id=registered_id,
    )
    await db.commit()

    assert merged_source_id == floating_id

    row = (
        await db.execute(
            text("SELECT is_active, merged_into_user_id FROM users WHERE id = :id"),
            {"id": floating_id},
        )
    ).fetchone()
    assert row[0] is False
    assert row[1] == registered_id


@pytest.mark.asyncio
async def test_no_merge_when_score_below_threshold(db):
    floating_id = await _create_floating(db, "Совсем Другое Имя")
    registered_id = await _create_registered(db, "Илья Рвачёв")

    merged_source_id = await check_and_merge_duplicate_on_registration(
        db, new_user_id=registered_id,
    )
    await db.commit()

    assert merged_source_id is None
    row = (
        await db.execute(text("SELECT is_active FROM users WHERE id = :id"), {"id": floating_id})
    ).fetchone()
    assert row[0] is True


@pytest.mark.asyncio
async def test_no_merge_when_floating_has_multiple_candidates(db):
    """Уникальность пары — обязательная защита select_auto_merge_pairs: у
    "плавающего" два похожих зарегистрированных кандидата → ручной разбор,
    не авто-слияние (иначе неоднозначно, с кем именно сливать)."""
    floating_id = await _create_floating(db, "Илья Рвачев")
    registered_a = await _create_registered(db, "Илья Рвачёв")
    registered_b = await _create_registered(db, "Илья Рвачев")

    merged_source_id = await check_and_merge_duplicate_on_registration(
        db, new_user_id=registered_a,
    )
    await db.commit()

    assert merged_source_id is None
    for uid in (floating_id, registered_a, registered_b):
        row = (
            await db.execute(text("SELECT is_active FROM users WHERE id = :id"), {"id": uid})
        ).fetchone()
        assert row[0] is True


@pytest.mark.asyncio
async def test_merges_within_same_uncommitted_registration_transaction(db):
    """Воспроизводит реальную обвязку auth-роутеров (tg.py/vk.py/magic_link.py):
    новый пользователь ещё НЕ закоммичен (только flush), дедуп-проверка
    вызывается в ТОЙ ЖЕ транзакции, коммит — один раз, в самом конце,
    как в роутере. Проверяет и autoflush-видимость нового юзера для
    find_duplicate_candidates, и что db.begin_nested() внутри merge_users
    не мешает финальному внешнему commit."""
    floating_id = await _create_floating(db, "Илья Рвачев")

    tg_id = random.SystemRandom().randint(10**12, 10**14)
    user, created = await get_or_create_user_by_tg(
        db, tg_id, full_name="Илья Рвачёв", ip=None, user_agent=None,
    )
    assert created is True

    merged_source_id = await check_and_merge_duplicate_on_registration(
        db, new_user_id=user.id,
    )
    await db.commit()  # тот самый финальный commit роутера

    assert merged_source_id == floating_id
    row = (
        await db.execute(
            text("SELECT is_active, merged_into_user_id FROM users WHERE id = :id"),
            {"id": floating_id},
        )
    ).fetchone()
    assert row[0] is False
    assert row[1] == user.id


@pytest.mark.asyncio
async def test_no_merge_when_new_account_has_no_matching_floating(db):
    registered_id = await _create_registered(db, "Уникальное Имя Фамилия")

    merged_source_id = await check_and_merge_duplicate_on_registration(
        db, new_user_id=registered_id,
    )
    assert merged_source_id is None
