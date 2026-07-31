"""tsk-433, 2026-07-30: слияние переносит карточные поля и освобождает почту.

Найдено живой проверкой на проде. После слияния дубля Астафьева попытка
проставить главной записи ту же почту через кабинет вернула **409 «такая почта
уже записана у другого человека»**: слитая запись оставалась владельцем адреса,
потому что `apply_merge` переносил только связанные строки, а `users.email` у
source не трогал. Частичный уникальный индекс на `users.email` считает и
неактивные записи — адрес оставался занятым навсегда.

Побочно терялось более полное ФИО: у дубля «Астафьев Данил Алексеевич», у
главной записи «Данил Астафьев» — после слияния оставалось короткое.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.user_merge_service import apply_merge


async def _user(db, name: str | None, email: str | None) -> int:
    u = Users(email=email, password_hash=None, full_name=name, tg_id=None)
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _row(db, uid: int):
    r = await db.execute(
        text("SELECT full_name, email, is_active, merged_into_user_id FROM users WHERE id=:i"),
        {"i": uid},
    )
    return r.first()


@pytest.mark.asyncio
async def test_email_moves_to_target_and_frees_source(db):
    """Почта переезжает на главную запись, у слитой очищается."""
    mail = f"t433c-{random.randint(10**8, 10**10)}@example.com"
    target = await _user(db, "Данил Астафьев", None)
    source = await _user(db, "Астафьев Данил Алексеевич", mail)

    await apply_merge(db, source, target)
    await db.commit()

    t, s = await _row(db, target), await _row(db, source)
    assert t.email == mail, "почта не переехала на главную запись"
    assert s.email is None, "у слитой записи почта осталась — адрес занят навсегда"
    assert s.is_active is False and s.merged_into_user_id == target


@pytest.mark.asyncio
async def test_target_email_is_not_overwritten(db):
    """Если у главной записи почта уже есть — она главнее.

    Иначе слияние молча подменило бы действующий контакт адресом дубля.
    """
    keep = f"t433c-keep-{random.randint(10**8, 10**10)}@example.com"
    other = f"t433c-other-{random.randint(10**8, 10**10)}@example.com"
    target = await _user(db, "Главный", keep)
    source = await _user(db, "Дубль", other)

    await apply_merge(db, source, target)
    await db.commit()

    assert (await _row(db, target)).email == keep


@pytest.mark.asyncio
async def test_fuller_name_wins(db):
    """Более полное ФИО сохраняется — оно обычно и есть настоящее."""
    target = await _user(db, "Данил Астафьев", None)
    source = await _user(db, "Астафьев Данил Алексеевич", None)

    await apply_merge(db, source, target)
    await db.commit()

    assert (await _row(db, target)).full_name == "Астафьев Данил Алексеевич"


@pytest.mark.asyncio
async def test_empty_name_is_filled_from_source(db):
    """Пустое имя у главной записи заполняется из дубля."""
    target = await _user(db, None, None)
    source = await _user(db, "Иванов Иван", None)

    await apply_merge(db, source, target)
    await db.commit()

    assert (await _row(db, target)).full_name == "Иванов Иван"


@pytest.mark.asyncio
async def test_freed_email_can_be_reused(db):
    """Освобождённый адрес снова можно записать живому человеку.

    Это и есть исходный симптом: 409 при правке карточки через кабинет.
    """
    mail = f"t433c-reuse-{random.randint(10**8, 10**10)}@example.com"
    target = await _user(db, "Главный", None)
    source = await _user(db, "Дубль", mail)
    third = await _user(db, "Третий", None)

    await apply_merge(db, source, target)
    await db.commit()

    # почта ушла к target; освобождать её у третьего человека не требуется —
    # проверяем, что повторная запись того же адреса не упирается в индекс
    await db.execute(text("UPDATE users SET email=NULL WHERE id=:i"), {"i": target})
    await db.commit()
    await db.execute(text("UPDATE users SET email=:m WHERE id=:i"), {"m": mail, "i": third})
    await db.commit()

    assert (await _row(db, third)).email == mail
