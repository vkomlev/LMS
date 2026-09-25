"""tsk-1123: поиск людей по имени — слова запроса через AND, порядок неважен, ё = е.

Случай с прода: «ксения скударнова» не находила «скударнова ксения алексеевна» —
`full_name ILIKE '%<запрос целиком>%'`. Правило одно для всех мест поиска людей
(`app/utils/name_search.py`): `/users/search`, поиск ученика для лида,
фильтр по ученику в очереди проверки.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import lead_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.utils.name_search import name_match_sql, split_search_words


def test_split_words_folds_case_and_yo() -> None:
    assert split_search_words("  Ксения   СКУДАРНОВА ") == ["ксения", "скударнова"]
    assert split_search_words("Алёна") == ["алена"]
    assert split_search_words("   ") == []
    assert split_search_words(None) == []


def test_name_match_sql_escapes_wildcards() -> None:
    sql, params = name_match_sql("u.full_name", "50% a_b", "p")
    assert sql.count("ILIKE") == 2
    assert params == {"p_0": "%50\\%%", "p_1": "%a\\_b%"}
    assert name_match_sql("u.full_name", "", "p") == ("TRUE", {})


async def _user(db, full_name: str, role: str) -> Users:
    u = Users(
        email=f"t1123-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=full_name,
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) SELECT :u, id FROM roles WHERE name = :r "
            "ON CONFLICT (user_id, role_id) DO NOTHING"
        ),
        {"u": u.id, "r": role},
    )
    await db.commit()
    return u


def _tag() -> str:
    # Уникальная «фамилия» из букв: изолирует тест от чужих людей в БД.
    return "т" + "".join(random.choice("бвгджзклмнпрст") for _ in range(10))


@pytest.mark.asyncio
async def test_users_search_word_order_and_yo(db, client) -> None:
    tag = _tag()
    target = await _user(db, f"{tag}ова Алёна Алексеевна", "student")
    other = await _user(db, f"{tag}ова Мария Ивановна", "student")
    methodist = await _user(db, "t1123-methodist", "methodist")
    token, _, _ = await create_session(db, user_id=methodist.id)
    await db.commit()
    headers = {"Authorization": f"Bearer {token}"}

    async def ids(q: str) -> set[int]:
        r = await client.get("/api/v1/users/search", params={"q": q, "limit": 200}, headers=headers)
        assert r.status_code == 200, r.text
        return {u["id"] for u in r.json()}

    # Имя раньше фамилии — прежде 0 результатов.
    assert await ids(f"алена {tag}ова") == {target.id}
    assert await ids(f"АЛЁНА {tag}") == {target.id}
    assert await ids(f"{tag} алекс") == {target.id}
    assert await ids(f"{tag}ова") == {target.id, other.id}
    # Слово, которого нет, отсекает всех: условия через AND, а не OR.
    assert await ids(f"{tag} ксения") == set()


@pytest.mark.asyncio
async def test_lead_student_search_word_order(db) -> None:
    tag = _tag()
    target = await _user(db, f"{tag}ова Ксения Алексеевна", "student")
    found = await lead_service.search_students(db, q=f"ксения {tag}ова", limit=20)
    assert [s.id for s in found] == [target.id]
    assert await lead_service.search_students(db, q=f"ксения {tag}_", limit=20) == []
