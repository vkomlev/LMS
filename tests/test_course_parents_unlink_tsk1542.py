"""tsk-1542: unlink ребра дерева курсов с младшими сиблингами не падает 500.

Найдено при tsk-1540: AFTER DELETE триггер `trg_reorder_course_parents_after_delete`
пересчитывал order_number младших соседей множественным UPDATE-ом БЕЗ флага
`app.skip_course_parent_order_trigger`. Если логический порядок детей не
совпадал с физическим (после одиночного PATCH order), вложенный BEFORE UPDATE
триггер пересчёта задевал ещё не обработанные кортежи → re-entrancy
(asyncpg TriggeredDataChangeViolationError «tuple to be updated was already
modified by an operation triggered by the current command» → 500).

Минимальный сценарий (проверен прямым SQL на клоне шаблона):
1. три потомка A, B, C с order 1, 2, 3;
2. PATCH order A → 3 (одиночный UPDATE; каскад идёт под флагом, физический
   порядок строк не меняется): логически B=1, C=2, A=3, физически A, B, C;
3. unlink B (order 1) — AFTER DELETE триггер UPDATE-ит C и A, вложенный
   пересчёт трогает ещё не обработанный кортеж → 500 до фикса.
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.courses import Courses
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_settings = Settings()


async def _user_with_session(db, role: str) -> tuple[int, str]:
    u = Users(
        email=f"t1542-{role}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"t1542-{role}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = :r ON CONFLICT DO NOTHING"
        ),
        {"u": u.id, "r": role},
    )
    await db.commit()
    return u.id, token


async def _course(db, tag: str) -> int:
    c = Courses(
        title=f"{tag}-{random.randint(10**8, 10**10)}",
        access_level="self_guided",
        course_uid=f"lms:test:t1542:{tag}:{uuid.uuid4().hex[:12]}",
    )
    db.add(c)
    await db.flush()
    await db.commit()
    return c.id


async def _attach(db, client, token: str, child: int, parent: int) -> None:
    r = await client.patch(
        f"/api/v1/courses/{child}/structure",
        json={"parent_course_ids": [parent], "replace_parents": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


async def _orders(db, parent: int) -> dict[int, int | None]:
    rows = (
        await db.execute(
            text(
                "SELECT course_id, order_number FROM course_parents "
                "WHERE parent_course_id = :p ORDER BY order_number"
            ),
            {"p": parent},
        )
    ).all()
    return {row.course_id: row.order_number for row in rows}


@pytest.mark.asyncio
async def test_unlink_after_reorder_falls_into_reentrancy(db, client):
    """PATCH order в конец + unlink нового первого — re-entrancy tsk-1542.

    До фикса PATCH structure отдавал 500 (TriggeredDataChangeViolationError
    из AFTER DELETE триггера); после — 200 и плотный пересчёт оставшихся.
    """
    parent = await _course(db, "parent")
    a = await _course(db, "a")
    b = await _course(db, "b")
    c = await _course(db, "c")
    _, token = await _user_with_session(db, "methodist")

    for child in (a, b, c):
        await _attach(db, client, token, child, parent)
    assert await _orders(db, parent) == {a: 1, b: 2, c: 3}

    # Опускаем первого ребёнка в конец (как tree_sync из tsk-1540).
    r = await client.patch(
        f"/api/v1/courses/{a}/parents/{parent}/order",
        json={"order_number": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert await _orders(db, parent) == {b: 1, c: 2, a: 3}

    # unlink нового первого: у него два младших соседа, логический порядок
    # не совпадает с физическим — до фикса здесь был 500.
    r = await client.patch(
        f"/api/v1/courses/{b}/structure",
        json={"parent_course_ids": [], "replace_parents": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    assert await _orders(db, parent) == {c: 1, a: 2}


@pytest.mark.asyncio
async def test_unlink_middle_child_resequences(db, client):
    """Отвязка среднего ребра — младший сдвигается, порядок остаётся плотным."""
    parent = await _course(db, "parent")
    c1 = await _course(db, "c1")
    c2 = await _course(db, "c2")
    c3 = await _course(db, "c3")
    _, token = await _user_with_session(db, "methodist")

    for child in (c1, c2, c3):
        await _attach(db, client, token, child, parent)

    r = await client.patch(
        f"/api/v1/courses/{c2}/structure",
        json={"parent_course_ids": [], "replace_parents": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    assert await _orders(db, parent) == {c1: 1, c3: 2}
