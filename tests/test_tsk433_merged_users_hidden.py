"""tsk-433, аудит 2026-07-30: слитые учётки не показываются как люди.

Что было: в списке людей кабинета методиста висели «дубли» — «Астафьев Данил
Алексеевич» и «Данил Астафьев», «Кузнецкий Кирилл Александрович» дважды и так
далее. Разведка показала, что автослияние (tsk-455) их УЖЕ обработало:
`is_active=false`, `merged_into_user_id` указывает на главную запись. То есть
дублей в данных нет — их рисовал список, потому что не фильтровал.

Отдельно вскрылось расхождение: поиск (`search_by_full_name_with_role`) уже
фильтровал по `is_active`, а список (`list_with_role_filter`) — нет. Один и тот
же человек то выглядел дублем, то не выглядел, смотря как его открыть. Теперь
критерий один.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.repos.users_repo import UsersRepository

_repo = UsersRepository()


async def _user(db, name: str, *, active: bool = True, merged_into: int | None = None) -> int:
    u = Users(
        email=f"t433m-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=name,
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    if not active or merged_into is not None:
        await db.execute(
            text(
                "UPDATE users SET is_active = :a, merged_into_user_id = :m WHERE id = :i"
            ),
            {"a": active, "m": merged_into, "i": u.id},
        )
    await db.commit()
    return u.id


@pytest.mark.asyncio
async def test_merged_user_is_not_listed(db):
    """Слитая запись — указатель на другого человека, а не человек."""
    main_id = await _user(db, f"Главный {random.randint(10**6, 10**8)}")
    merged_id = await _user(db, "Слитый дубль", active=False, merged_into=main_id)

    items, _ = await _repo.list_with_role_filter(db, limit=1000)
    ids = [u.id for u in items]

    assert main_id in ids
    assert merged_id not in ids, "слитая учётка попала в список как отдельный человек"


@pytest.mark.asyncio
async def test_total_count_matches_visible_rows(db):
    """Счётчик «всего» считает то же, что показано.

    Иначе методист видит «Всего: 46», листает и находит 44 — и не понимает,
    куда делись двое.
    """
    main_id = await _user(db, f"Счётный {random.randint(10**6, 10**8)}")
    await _user(db, "Слитый для счёта", active=False, merged_into=main_id)

    items, total = await _repo.list_with_role_filter(db, limit=1000)
    assert total == len(items), f"счётчик {total} расходится с показанным {len(items)}"


@pytest.mark.asyncio
async def test_list_and_search_agree_on_who_exists(db):
    """Список и поиск отвечают одинаково — раньше фильтровал только поиск."""
    name = f"Согласный {random.randint(10**6, 10**8)}"
    main_id = await _user(db, name)
    merged_id = await _user(db, name, active=False, merged_into=main_id)

    items, _ = await _repo.list_with_role_filter(db, limit=1000)
    found = await _repo.search_by_full_name_with_role(db, q=name, limit=50)

    listed = {u.id for u in items if u.id in (main_id, merged_id)}
    searched = {u.id for u in found if u.id in (main_id, merged_id)}
    assert listed == searched == {main_id}
