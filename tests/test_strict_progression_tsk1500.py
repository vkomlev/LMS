"""tsk-1500: жёсткая последовательность подкурсов.

Постановка (решение оператора 11.11): пока в предыдущих подкурсах есть
нерешённые обязательные задания (накопительно), движок не отдаёт элементы
следующего подкурса — возвращает blocked_dependency с блокирующим разделом.
Настройка живёт на корневом курсе (`courses.strict_progression`), ручной
обход методистом — `progression_overrides`.

Сценарий — дословно жалоба QA из tsk-261 (A5): ученик оставил обязательное
задание подкурса 1 нерешённым, ушёл вперёд. Без настройки движок подаёт
контент подкурса 2 (прежнее поведение зафиксировано тестом tsk-261); с
настройкой — замок с указанием закрыть подкурс 1.
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.learning_engine_service import LearningEngineService


async def _student(db) -> int:
    email = f"tsk1500-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="tsk1500", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, is_required, course_uid) "
            "VALUES (:t, 'self_guided', false, :uid) RETURNING id"
        ),
        {"t": title, "uid": f"tsk1500-{random.randint(10**8, 10**10)}"},
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


async def _task(db, *, course_id: int, order_position: int, level: str = "required") -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
            " difficulty_id, order_position, is_active, requirement_level) "
            "VALUES (:uid, 10, CAST(:tc AS jsonb), :c, "
            " (SELECT MIN(id) FROM difficulties), :o, true, :level) RETURNING id"
        ),
        {
            "uid": f"tsk1500-{random.randint(10**8, 10**10)}",
            "tc": '{"type": "SA", "stem": "test"}',
            "c": course_id,
            "o": order_position,
            "level": level,
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


async def _pass_task(db, *, student_id: int, task_id: int, score: int = 10) -> None:
    """PASSED по семантике движка: последняя сданная попытка, score*2 >= max_score."""
    await db.execute(
        text(
            "INSERT INTO task_results (task_id, user_id, score, submitted_at, received_at) "
            "VALUES (:t, :u, :s, NOW(), NOW())"
        ),
        {"t": task_id, "u": student_id, "s": score},
    )
    await db.commit()


async def _set_strict(db, *, root_id: int, value: bool) -> None:
    await db.execute(
        text("UPDATE courses SET strict_progression = :v WHERE id = :id"),
        {"v": value, "id": root_id},
    )
    await db.commit()


@pytest.fixture
async def tree(db):
    """root(strict off) → [child1(m1, t1), child2(m3, t2)] + ученик на корне."""
    user_id = await _student(db)
    root = await _course(db, "tsk1500 root")
    child1 = await _course(db, "tsk1500 child1")
    child2 = await _course(db, "tsk1500 child2")
    await _link(db, course_id=child1, parent_course_id=root, order_number=1)
    await _link(db, course_id=child2, parent_course_id=root, order_number=2)

    m1 = await _material(db, course_id=child1, order_position=1, title="c1-m1")
    t1 = await _task(db, course_id=child1, order_position=1)
    m3 = await _material(db, course_id=child2, order_position=1, title="c2-m3")
    t2 = await _task(db, course_id=child2, order_position=1)

    await _enroll(db, user_id, root)
    data = {
        "user_id": user_id, "root": root, "child1": child1, "child2": child2,
        "m1": m1, "t1": t1, "m3": m3, "t2": t2,
    }
    yield data

    # Пользователя не удаляем: create_session пишет в audit_event, а он
    # append-only — каскад от users на нём падает (паттерн test_y62_*/tsk261).
    await db.execute(text("DELETE FROM progression_overrides WHERE root_course_id = :r"), {"r": root})
    await db.execute(text("DELETE FROM task_results WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM student_material_progress WHERE student_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM user_courses WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = :u"), {"u": user_id})
    await db.execute(text("DELETE FROM materials WHERE course_id = ANY(:ids)"), {"ids": [child1, child2]})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:ids)"), {"ids": [child1, child2]})
    await db.execute(text("DELETE FROM course_parents WHERE parent_course_id = :r"), {"r": root})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:ids)"), {"ids": [child1, child2, root]})
    await db.commit()


@pytest.mark.asyncio
async def test_setting_off_keeps_baseline(db, tree):
    """Выключенная настройка (дефолт): поведение tsk-261 — вперёд без замка."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])

    res = await svc.resolve_next_item(db, uid, root_course_id=root, after_material_id=tree["m3"])
    assert res.type == "task" and res.task_id == tree["t2"], f"без настройки замка нет: {res}"


@pytest.mark.asyncio
async def test_gate_blocks_until_required_passed(db, tree):
    """Настройка включена: t1 в подкурсе 1 нерешено → дальше подкурс 2 закрыт.

    Ученик прошёл m1, ушёл вперёд (m3), t1 не решал. Движок обязан вернуть
    blocked_dependency с подкурсом 1 — и отпустить после зачёта t1.
    """
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _set_strict(db, root_id=root, value=True)
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])

    blocked = await svc.resolve_next_item(db, uid, root_course_id=root, after_material_id=tree["m3"])
    assert blocked.type == "blocked_dependency", f"ожидали замок: {blocked}"
    assert blocked.dependency_course_id == tree["child1"], blocked
    assert blocked.dependency_course_title and blocked.dependency_course_uid is not None

    await _pass_task(db, student_id=uid, task_id=tree["t1"])
    free = await svc.resolve_next_item(db, uid, root_course_id=root, after_material_id=tree["m3"])
    assert free.type == "task" and free.task_id == tree["t2"], f"после зачёта замок снят: {free}"


@pytest.mark.asyncio
async def test_recommended_does_not_block(db, tree):
    """«Желательные» задания гейт не держат — только обязательные."""
    svc = LearningEngineService()
    uid = tree["user_id"]
    root, child1 = tree["root"], tree["child1"]
    t_rec = await _task(db, course_id=child1, order_position=2, level="recommended")
    await _set_strict(db, root_id=root, value=True)
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _pass_task(db, student_id=uid, task_id=tree["t1"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])

    res = await svc.resolve_next_item(db, uid, root_course_id=root, after_material_id=tree["m3"])
    assert res.type == "task" and res.task_id == tree["t2"], (
        f"нерешённое «желательное» не должно держать: {res}"
    )
    await db.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": t_rec})
    await db.commit()


@pytest.mark.asyncio
async def test_override_releases_student(db, tree):
    """Ручная разблокировка методистом (progression_overrides) отпускает вперёд."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _set_strict(db, root_id=root, value=True)
    await _complete_material(db, student_id=uid, material_id=tree["m1"])
    await _complete_material(db, student_id=uid, material_id=tree["m3"])
    await db.execute(
        text(
            "INSERT INTO progression_overrides (root_course_id, user_id) "
            "VALUES (:r, :u) ON CONFLICT DO NOTHING"
        ),
        {"r": root, "u": uid},
    )
    await db.commit()

    res = await svc.resolve_next_item(db, uid, root_course_id=root, after_material_id=tree["m3"])
    assert res.type == "task" and res.task_id == tree["t2"], f"override должен отпускать: {res}"


@pytest.mark.asyncio
async def test_first_node_never_blocked(db, tree):
    """Гейт накопительный: первый подкурс доступен всегда, настройка не мешает старту."""
    svc = LearningEngineService()
    uid, root = tree["user_id"], tree["root"]
    await _set_strict(db, root_id=root, value=True)

    res = await svc.resolve_next_item(db, uid, root_course_id=root)
    assert res.type == "material" and res.material_id == tree["m1"], f"старт закрыт: {res}"
