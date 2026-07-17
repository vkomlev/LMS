"""tsk-261: узел под НЕСКОЛЬКИМИ родителями не должен двоиться.

Найдено независимым ревью перед деплоем — обе находки блокирующие, обе
подтверждены на проде:

1. `_collect_courses_in_order` обходил дерево без множества посещённых, поэтому
   узел с несколькими родителями попадал в `flat_courses` несколько раз (прод:
   839/843/1020/1054 — по 2 раза в дереве ОГЭ; 1247 — 5 раз в дереве 871).
   `flat_courses.index(курс_позиции)` брал ПЕРВОЕ вхождение → ученика со второго
   вхождения отбрасывало назад, то есть позиционный обход (A4/A5) на таких
   курсах не работал и маскировал сам себя.

2. `me_service._COURSES_PROGRESS_SQL`: `course_trees` — рекурсия по
   `course_parents`, даёт строку НА КАЖДЫЙ ПУТЬ до узла, а не на узел. Без
   DISTINCT `COUNT(*)` считал фантомы: на проде у курса 871 — 220 заданий против
   172 реальных (+28%). Процент оставался кривым даже после снятия фильтра
   `finished_at`, то есть жалоба «неверный процент» закрылась бы не везде.

Дерево теста: root → [A, B]; узел S подвешен И к A, И к B (2 пути до S).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService


async def _course(db, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk261reuse-{random.randint(10**8, 10**10)}"},
    )
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _link(db, *, course_id: int, parent_course_id: int, order_number: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, :o) ON CONFLICT DO NOTHING"
        ),
        {"c": course_id, "p": parent_course_id, "o": order_number},
    )
    await db.commit()


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
            "uid": f"tsk261reuse-{random.randint(10**8, 10**10)}",
            "tc": '{"type": "SA", "stem": "test"}',
            "c": course_id,
            "o": order_position,
        },
    )
    tid = int(res.scalar_one())
    await db.commit()
    return tid


@pytest.fixture
async def reused(db):
    """root → [A, B]; S — общий подкурс A и B (два пути до S)."""
    email = f"tsk261reuse-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk261reuse", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    user_id = u.id

    root = await _course(db, "tsk261 reuse root")
    a = await _course(db, "tsk261 reuse A")
    b = await _course(db, "tsk261 reuse B")
    s = await _course(db, "tsk261 reuse S (общий)")
    await _link(db, course_id=a, parent_course_id=root, order_number=1)
    await _link(db, course_id=b, parent_course_id=root, order_number=2)
    await _link(db, course_id=s, parent_course_id=a, order_number=1)
    await _link(db, course_id=s, parent_course_id=b, order_number=1)

    s_m = await _material(db, course_id=s, order_position=1, title="s-m")
    s_t = await _task(db, course_id=s, order_position=1)
    b_m = await _material(db, course_id=b, order_position=1, title="b-m")

    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "c": root},
    )
    await db.commit()

    data = {
        "user_id": user_id, "token": token, "root": root,
        "a": a, "b": b, "s": s, "s_m": s_m, "s_t": s_t, "b_m": b_m,
    }
    yield data

    ids = [root, a, b, s]
    await db.execute(text("DELETE FROM student_material_progress WHERE student_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": user_id})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:ids)"), {"ids": ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": ids})
    await db.execute(text("DELETE FROM course_parents WHERE course_id = ANY(:ids)"), {"ids": ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": ids})
    await db.commit()


@pytest.mark.asyncio
async def test_flat_courses_deduplicated(db, reused):
    """Общий узел входит в обход РОВНО один раз (иначе index() врёт)."""
    svc = LearningEngineService()
    flat = await svc._collect_courses_in_order(db, reused["root"])
    assert flat.count(reused["s"]) == 1, f"общий узел задвоился: {flat}"
    assert len(flat) == len(set(flat)), f"в обходе есть дубли: {flat}"


@pytest.mark.asyncio
async def test_position_in_reused_node_goes_forward(db, reused):
    """Позиция в общем узле не отбрасывает назад: после s_m → s_t, не начало."""
    svc = LearningEngineService()
    res = await svc.resolve_next_item(
        db, reused["user_id"], root_course_id=reused["root"], after_material_id=reused["s_m"]
    )
    assert res.type == "task" and res.task_id == reused["s_t"], res


@pytest.mark.asyncio
async def test_progress_totals_not_inflated_by_reuse(db, reused):
    """tasks_total/materials_total считают УЗЛЫ, а не пути (иначе процент кривой).

    S достижим двумя путями (через A и через B). Без DISTINCT его 1 задание и
    1 материал посчитались бы дважды.
    """
    from app.services import me_service

    courses = await me_service.get_courses_with_progress(db, reused["user_id"])
    entry = next((c for c in courses if c["course_id"] == reused["root"]), None)
    assert entry is not None, "курс должен быть в /me/courses"
    progress = entry["progress"]
    assert progress["tasks_total"] == 1, (
        f"задание общего узла посчитано по числу путей: {progress}"
    )
    assert progress["materials_total"] == 2, (
        f"материалы (s_m + b_m) посчитаны по числу путей: {progress}"
    )
