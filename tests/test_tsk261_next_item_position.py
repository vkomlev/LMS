"""tsk-261 (A4/A5): next-item ищет следующий шаг ОТ ТЕКУЩЕЙ ПОЗИЦИИ ученика.

Дефект приёмки QA 2026-07-16:
- A5: «не решила задание → перешла на новый блок → "Прошёл материал" → редирект
  не на следующий блок, а на ПРЕДЫДУЩЕЕ невыполненное задание»;
- A4: «"Материал пройден" → переходим в раздел 1.5, Задание 1 пропускается»
  (ожидалось: возврат в список разделов).

Первопричина одна: `resolve_next_item` не знал позицию ученика и всегда отдавал
ПЕРВЫЙ незавершённый элемент по всему дереву курса.

Строим дерево: root → [child1(2 материала, 1 задание), child2(2 материала)].
Порядок обхода post-order: child1(мат) → child1(зад) → child2(мат) → root(мат).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService


async def _student(db) -> int:
    email = f"tsk261pos-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk261pos", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _token(db, user_id: int) -> str:
    token, _, _ = await create_session(db, user_id=user_id)
    await db.commit()
    return token


async def _course(db, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk261pos-{random.randint(10**8, 10**10)}"},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _material(db, *, course_id: int, order_position: int, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (course_id, type, content, order_position, title, "
            " is_active, requirement_level) "
            "VALUES (:c, 'text', CAST('{}' AS jsonb), :o, :t, true, 'required') RETURNING id"
        ),
        {"c": course_id, "o": order_position, "t": title},
    )
    mid = int(res.scalar_one())
    await db.commit()
    return mid


async def _task(db, *, course_id: int, order_position: int) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
            " difficulty_id, order_position, is_active, requirement_level) "
            "VALUES (:uid, 10, CAST(:tc AS jsonb), :c, "
            " (SELECT MIN(id) FROM difficulties), :o, true, 'required') RETURNING id"
        ),
        {
            "uid": f"tsk261pos-{random.randint(10**8, 10**10)}",
            "tc": '{"type": "SA", "stem": "test"}',
            "c": course_id,
            "o": order_position,
        },
    )
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _link(db, *, course_id: int, parent_course_id: int, order_number: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, :o) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id, "o": order_number},
    )
    await db.commit()


async def _enroll(db, user_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": course_id},
    )
    await db.commit()


async def _complete_material(db, *, student_id: int, material_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress (student_id, material_id, status, completed_at) "
            "VALUES (:s, :m, 'completed', NOW()) "
            "ON CONFLICT (student_id, material_id) DO UPDATE SET status='completed'"
        ),
        {"s": student_id, "m": material_id},
    )
    await db.commit()


@pytest.fixture
async def tree(db):
    """root → [child1(m1,m2,t1), child2(m3,m4)] + собственный материал root(m5)."""
    user_id = await _student(db)
    root = await _course(db, "tsk261 root")
    child1 = await _course(db, "tsk261 child1")
    child2 = await _course(db, "tsk261 child2")
    await _link(db, course_id=child1, parent_course_id=root, order_number=1)
    await _link(db, course_id=child2, parent_course_id=root, order_number=2)

    m1 = await _material(db, course_id=child1, order_position=1, title="c1-m1")
    m2 = await _material(db, course_id=child1, order_position=2, title="c1-m2")
    t1 = await _task(db, course_id=child1, order_position=1)
    m3 = await _material(db, course_id=child2, order_position=1, title="c2-m3")
    m4 = await _material(db, course_id=child2, order_position=2, title="c2-m4")
    m5 = await _material(db, course_id=root, order_position=1, title="root-m5")

    await _enroll(db, user_id, root)
    data = {
        "user_id": user_id, "root": root, "child1": child1, "child2": child2,
        "m1": m1, "m2": m2, "t1": t1, "m3": m3, "m4": m4, "m5": m5,
    }
    yield data

    # Пользователя НЕ удаляем: create_session пишет в audit_event, а он
    # append-only (триггер БД) — каскад от users на нём падает. Тестовый юзер
    # остаётся в dev-БД, как и в остальных тестах (паттерн test_y62_*).
    await db.execute(text("DELETE FROM student_material_progress WHERE student_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:ids)"), {"ids": [root, child1, child2]})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": [root, child1, child2]})
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id=:r"), {"r": root})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": [child1, child2, root]})
    await db.commit()


@pytest.mark.asyncio
async def test_a5_no_jump_back_to_earlier_incomplete(db, tree):
    """A5: с позиции m3 следующий шаг — m4, а НЕ задание t1 из прошлого блока.

    Ученик прошёл m1, m2, оставил t1 нерешённым, ушёл в child2 и отметил m3.
    До tsk-261 движок возвращал t1 (первый недоделанный по дереву) — ученика
    отбрасывало назад. Это дословно жалоба QA.
    """
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m2"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])

    # Прежнее поведение (без позиции) — тащит назад на t1.
    old = await svc.resolve_next_item(db, uid, root_course_id=root)
    assert old.type == "task" and old.task_id == tree["t1"], (
        f"фиксируем прежнее поведение: {old}"
    )

    # С позицией — идём вперёд, к m4.
    new = await svc.resolve_next_item(
        db, uid, root_course_id=root, after_material_id=tree["m3"]
    )
    assert new.type == "material", new
    assert new.material_id == tree["m4"], (
        f"ожидали следующий материал того же блока (m4), получили {new}"
    )


@pytest.mark.asyncio
async def test_a4_end_of_course_returns_none(db, tree):
    """A4: позиция — последний элемент обхода (материал корня) → впереди пусто.

    SPW на type=none возвращает ученика в список разделов — ожидаемый QA результат.
    """
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    for m in ("m1", "m2", "m3", "m4", "m5"):
        await _complete_material(db, student_id=uid, material_id=tree[m])

    res = await svc.resolve_next_item(
        db, uid, root_course_id=root, after_material_id=tree["m5"]
    )
    assert res.type == "none", f"после последнего элемента впереди ничего нет: {res}"


@pytest.mark.asyncio
async def test_position_in_same_course_goes_to_own_task(db, tree):
    """С позиции m2 (последний материал child1) следующий шаг — задание t1 того же курса."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m2"])

    res = await svc.resolve_next_item(
        db, uid, root_course_id=root, after_material_id=tree["m2"]
    )
    assert res.type == "task" and res.task_id == tree["t1"], res


@pytest.mark.asyncio
async def test_position_task_skips_own_materials(db, tree):
    """С позиции t1 материалы child1 уже позади → идём в child2 (m3)."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    # m1/m2 НЕ отмечены: они позади позиции и не должны тянуть назад.
    res = await svc.resolve_next_item(
        db, uid, root_course_id=root, after_task_id=tree["t1"]
    )
    assert res.type == "material" and res.material_id == tree["m3"], res


@pytest.mark.asyncio
async def test_unknown_position_falls_back_to_first_incomplete(db, tree):
    """Позиция вне дерева → прежнее поведение (первый незавершённый с начала)."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    res = await svc.resolve_next_item(
        db, uid, root_course_id=root, after_material_id=999_999_999
    )
    assert res.type == "material" and res.material_id == tree["m1"], res


@pytest.mark.asyncio
async def test_api_passes_position_through(db, client, tree):
    """Проводка эндпоинта: query-параметр after_material_id доходит до движка.

    Юнит-тесты выше зовут сервис напрямую; этот идёт через реальный HTTP-слой —
    иначе забытый проброс параметра в `api/v1/learning.py` остался бы незамеченным.
    """
    uid, root = tree["user_id"], tree["root"]
    token = await _token(db, uid)
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m2"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])
    headers = {"Authorization": f"Bearer {token}"}

    # Без позиции — тащит назад на невыполненное задание t1.
    r_old = await client.get(
        f"/api/v1/learning/next-item?student_id={uid}&root_course_id={root}",
        headers=headers,
    )
    assert r_old.status_code == 200, r_old.text
    assert r_old.json()["task_id"] == tree["t1"], r_old.json()

    # С позицией — вперёд, на m4.
    r_new = await client.get(
        f"/api/v1/learning/next-item?student_id={uid}&root_course_id={root}"
        f"&after_material_id={tree['m3']}",
        headers=headers,
    )
    assert r_new.status_code == 200, r_new.text
    body = r_new.json()
    assert body["type"] == "material" and body["material_id"] == tree["m4"], body


@pytest.mark.asyncio
async def test_blocked_limit_still_detected_forward(db, tree):
    """Позиционный обход не теряет blocked_limit впереди по курсу."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m2"])
    # Исчерпать лимит по t1: 3 результата с провалом в открытой попытке.
    # tsk-264: попытка несёт корень обхода — лимит считается в его границах.
    res_a = await db.execute(
        text(
            "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
            "VALUES (:u, :c, :rc, 'spw') RETURNING id"
        ),
        {"u": uid, "c": tree["child1"], "rc": root},
    )
    aid = int(res_a.scalar_one())
    for _ in range(3):
        await db.execute(
            text(
                "INSERT INTO task_results (score, user_id, task_id, attempt_id, submitted_at, "
                " count_retry, received_at, max_score, source_system, is_correct) "
                "VALUES (0, :u, :t, :a, NOW(), 0, NOW(), 10, 'spw', false)"
            ),
            {"u": uid, "t": tree["t1"], "a": aid},
        )
    await db.commit()
    try:
        res = await svc.resolve_next_item(
            db, uid, root_course_id=root, after_material_id=tree["m2"]
        )
        assert res.type == "blocked_limit" and res.task_id == tree["t1"], res
    finally:
        await db.execute(text("DELETE FROM task_results WHERE attempt_id=:a"), {"a": aid})
        await db.execute(text("DELETE FROM attempts WHERE id=:a"), {"a": aid})
        await db.commit()
