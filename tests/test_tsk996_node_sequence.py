"""tsk-996 (фаза A): общая `node_sequence` даёт ровно прежний порядок.

ADR-0008, постановка `docs/briefs/2026-10-19-p7-anchor-implementation.md`, шаг 4.

Фаза A — рефакторинг без изменения поведения: шесть мест, собиравших порядок
«материалы узла → подразделы → задания узла», переведены на одну функцию, а
прежний код оставлен за рубильником `NODE_SEQUENCE_ENABLED=false`. Поэтому
проверка здесь одна и та же для всех форм дерева: снимок выдачи при `true` и
при `false` совпадает целиком. В снимок входит всё, что зависит от порядка:

* программа курса `syllabus-states` (весь ответ);
* полная цепочка next-item от начала дерева: каждый следующий шаг — с
  `after_material_id`/`after_task_id` предыдущего (и та же цепочка без корня);
* next-item от КАЖДОГО элемента дерева как позиции — включая неактивные и
  `recommended`, которых в обходе нет (курсор режет по ключу, а не по списку);
* предпросмотр «глазами ученика» от каждого элемента;
* карточка прогресса ученика у преподавателя;
* правило «ушёл вперёд» (tsk-1261) и итог прощённого (tsk-692/1361);
* список курсов дерева (post-order, tsk-127).

Порядок прогонов — `true`, `false`, `true`: next-item пишет состояние курсов
зависимостей (`student_course_state`), и тройной прогон заодно показывает, что
результат не зависит от того, какой путь шёл первым.

Плюс замер числа SQL-запросов одного next-item: не больше прежнего (кеш обхода
tsk-662/1250 сохранён).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from app.repos.courses_repo import invalidate_course_tree_cache
from app.services import manual_progress_service, me_service
from app.services.content_grace_service import (
    compute_grace_reasons,
    compute_graced_items,
    invalidate_grace_cache,
)
from app.services.learning_engine_service import LearningEngineService
from app.services.node_sequence import (
    HEAD,
    TAIL,
    node_sequence,
    walk_steps,
)
from app.services.preview_navigation import preview_next_item
from app.services.requirement_override_service import set_human_override
from app.services.transparent_subcourse import task_key

LONG_AGO = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
INSERTED = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
CHAIN_LIMIT = 200


# ─── Построение данных ──────────────────────────────────────────────────────


async def _course(db, title: str, *, sampling: Optional[str] = None) -> int:
    res = await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid, sampling_config) "
            "VALUES (:t, 'self_guided', :uid, CAST(:cfg AS jsonb)) RETURNING id"
        ),
        {"t": f"tsk996 {title}", "uid": f"tsk996-{uuid4().hex[:12]}", "cfg": sampling},
    )
    return int(res.scalar_one())


async def _link(
    db, parent_id: int, child_id: int, order_number: Optional[int], *, transparent: bool = False
) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number, is_transparent) "
            "VALUES (:c, :p, :o, :tr)"
        ),
        {"c": child_id, "p": parent_id, "o": order_number, "tr": transparent},
    )


async def _material(
    db,
    course_id: int,
    position: Optional[int],
    *,
    level: str = "required",
    active: bool = True,
    created_at: datetime = LONG_AGO,
) -> int:
    res = await db.execute(
        text(
            "INSERT INTO materials (title, type, content, course_id, order_position, "
            " is_active, requirement_level, created_at, updated_at) "
            "VALUES (:t, 'text', CAST('{}' AS jsonb), :c, :p, :a, :lvl, :ts, :ts) RETURNING id"
        ),
        {
            "t": f"tsk996 м {uuid4().hex[:6]}", "c": course_id, "p": position,
            "a": active, "lvl": level, "ts": created_at,
        },
    )
    return int(res.scalar_one())


async def _difficulty(db, code: str) -> int:
    return int(
        (await db.execute(text("SELECT id FROM difficulties WHERE code = :c"), {"c": code}))
        .scalar_one()
    )


async def _task(
    db,
    course_id: int,
    position: Optional[int],
    *,
    level: str = "required",
    active: bool = True,
    difficulty: str = "NORMAL",
    created_at: datetime = LONG_AGO,
    host_position: Optional[int] = None,
) -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid, "
            " order_position, is_active, requirement_level, created_at, host_order_position) "
            "VALUES (CAST(:tc AS jsonb), :c, :d, :uid, :p, :a, :lvl, :ts, :hp) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "stem": "tsk996"}',
            "c": course_id,
            "d": await _difficulty(db, difficulty),
            "uid": f"tsk996-{uuid4().hex[:12]}",
            "p": position,
            "a": active,
            "lvl": level,
            "ts": created_at,
            "hp": host_position,
        },
    )
    return int(res.scalar_one())


async def _student(db, *roots: int) -> int:
    uid = int(
        (
            await db.execute(
                text("INSERT INTO users (full_name) VALUES (:n) RETURNING id"),
                {"n": f"tsk996 ученик {uuid4().hex[:6]}"},
            )
        ).scalar_one()
    )
    for order, root in enumerate(roots, start=1):
        await db.execute(
            text(
                "INSERT INTO user_courses (user_id, course_id, is_active, order_number) "
                "VALUES (:u, :c, true, :o)"
            ),
            {"u": uid, "c": root, "o": order},
        )
    return uid


async def _complete_material(db, student: int, material_id: int, at: datetime = LONG_AGO) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "(student_id, material_id, status, completed_at, source) "
            "VALUES (:u, :m, 'completed', :ts, 'system')"
        ),
        {"u": student, "m": material_id, "ts": at},
    )


async def _result(
    db, student: int, task_id: int, course_id: int, root_id: int,
    *, correct: bool = True, at: datetime = LONG_AGO, times: int = 1,
) -> None:
    attempt_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                    "VALUES (:u, :c, :r, 'spw') RETURNING id"
                ),
                {"u": student, "c": course_id, "r": root_id},
            )
        ).scalar_one()
    )
    for _ in range(times):
        await db.execute(
            text(
                "INSERT INTO task_results (score, user_id, task_id, attempt_id, submitted_at, "
                " count_retry, received_at, max_score, source_system, is_correct) "
                "VALUES (:s, :u, :t, :a, :ts, 0, :ts, 10, 'spw', :ok)"
            ),
            {
                "s": 10 if correct else 0, "u": student, "t": task_id,
                "a": attempt_id, "ts": at, "ok": correct,
            },
        )


async def _skip_task(db, student: int, task_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_task_progress (student_id, task_id, status) "
            "VALUES (:u, :t, 'skipped')"
        ),
        {"u": student, "t": task_id},
    )


# ─── Формы дерева ───────────────────────────────────────────────────────────
# Каждая форма возвращает (корни для снимка, ученик).


async def shape_only_materials(db) -> tuple[list[int], int]:
    """Узел только из материалов: свои позиции не по порядку id, неактивный, рекомендуемый."""
    root = await _course(db, "только материалы")
    m3 = await _material(db, root, 3)
    m1 = await _material(db, root, 1)
    await _material(db, root, 2, level="recommended")
    await _material(db, root, 4, active=False)
    await _material(db, root, 5, level="skippable")
    no_pos = await _material(db, root, 6)
    # Материал без позиции (NULLS LAST) — если триггер позволяет её снять.
    await db.execute(
        text("UPDATE materials SET order_position = NULL WHERE id = :m"), {"m": no_pos}
    )
    student = await _student(db, root)
    await _complete_material(db, student, m1)
    del m3
    return [root], student


async def shape_only_tasks(db) -> tuple[list[int], int]:
    """Узел только из заданий: решённое, пропущенное, неактивное, рекомендуемое, лимит."""
    root = await _course(db, "только задания")
    # Позиции не по порядку id: порядок обязан идти по позиции.
    t5 = await _task(db, root, 1)
    await _task(db, root, 1, level="recommended")
    t1 = await _task(db, root, 1)
    await _task(db, root, 2, active=False)
    t6 = await _task(db, root, 1, level="skippable")
    t2 = await _task(db, root, 3)
    await _task(db, root, 2)
    student = await _student(db, root)
    await _result(db, student, t1, root, root)
    await _skip_task(db, student, t2)
    # Исчерпанный лимит: три неверных ответа (по умолчанию лимит — 3).
    await _result(db, student, t5, root, root, correct=False, times=3)
    await _result(db, student, t6, root, root, correct=False)
    return [root], student


async def shape_nested(db) -> tuple[list[int], int]:
    """Подразделы: свои материалы и задания у каждого уровня, переиспользованный узел.

    root (м, м, з, з)
      ├─ c2 (номер 1): g (м, з), свои задания
      ├─ c1 (номер 2): м, з, и тот же g ещё раз (tsk-261 — обходится один раз)
      └─ c3 (номер 3): пустой
    """
    root = await _course(db, "вложенные корень")
    c1 = await _course(db, "вложенные c1")
    c2 = await _course(db, "вложенные c2")
    c3 = await _course(db, "вложенные c3 пустой")
    g = await _course(db, "вложенные g")
    await _link(db, root, c2, 1)
    await _link(db, root, c1, 2)
    await _link(db, root, c3, 3)
    await _link(db, c2, g, 1)
    await _link(db, c1, g, 2)

    rm1 = await _material(db, root, 1)
    await _material(db, root, 2)
    await _task(db, root, 1)
    rt2 = await _task(db, root, 2)
    await _material(db, c1, 1)
    c1t = await _task(db, c1, 1)
    await _task(db, c1, 2, level="recommended")
    await _task(db, c2, 1)
    await _task(db, c2, 2)
    gm = await _material(db, g, 1)
    await _material(db, g, 2, active=False)
    await _task(db, g, 1)
    await _task(db, g, 2)
    student = await _student(db, root)
    await _complete_material(db, student, rm1)
    await _complete_material(db, student, gm)
    await _result(db, student, c1t, c1, root)
    await _result(db, student, rt2, root, root)
    return [root], student


async def shape_transparent(db) -> tuple[list[int], int]:
    """Прозрачный подкурс tsk-1198: задания подборки на своих местах у хозяина.

    bank (м1, м2, T1..T3) ← sub прозрачен (X1 между T1 и T2, X2 после T3, свой
    материал на позиции 1 — у хозяина он идёт группой своего курса, после м2);
    prak — обычный корень, где sub — раздел.
    """
    bank = await _course(db, "банк")
    sub = await _course(db, "подборка")
    prak = await _course(db, "практикум")
    await _link(db, bank, sub, 1, transparent=True)
    await _link(db, prak, sub, 1)
    await _material(db, bank, 1)
    await _material(db, bank, 2)
    t1 = await _task(db, bank, 1)
    await _task(db, bank, 2)
    await _task(db, bank, 3)
    await _material(db, sub, 1)
    x1 = await _task(db, sub, 1, host_position=200)
    await _task(db, sub, 2, host_position=400)
    await _task(db, sub, 3, host_position=None)
    student = await _student(db, bank, prak)
    await _result(db, student, t1, bank, bank)
    await _result(db, student, x1, sub, prak)
    return [bank, prak], student


async def shape_sampling(db) -> tuple[list[int], int]:
    """Выборка tsk-314: часть EASY/NORMAL заданий вырезана для ученика."""
    root = await _course(db, "выборка корень")
    topic = await _course(
        db, "выборка тема",
        sampling='{"enabled": true, "threshold": 3, "easy_ratio": 0.5}',
    )
    await _link(db, root, topic, 1)
    await _material(db, topic, 1)
    for pos in range(1, 5):
        await _task(db, topic, pos, difficulty="EASY")
    for pos in range(5, 9):
        await _task(db, topic, pos, difficulty="NORMAL")
    await _task(db, topic, 9, difficulty="HARD")
    await _task(db, root, 1, difficulty="THEORY")
    student = await _student(db, root)
    return [root], student


async def shape_grace(db) -> tuple[list[int], int]:
    """Grace tsk-692/1261: вставленное позади ученика — не долг.

    Тема: старые задания 1, 2 (не решены) и 3 (решено давно), материал. Потом
    на позицию 2 вставлено задание, на позицию 2 материалов — материал. Правило
    «ушёл вперёд» прощает вставку только потому, что ПОСЛЕ неё в порядке узла
    стоит решённое задание; перед ней решённого нет — прощение зависит от
    порядка внутри узла, а не только от множества элементов.
    """
    root = await _course(db, "grace корень")
    topic = await _course(db, "grace тема")
    after = await _course(db, "grace после")
    await _link(db, root, topic, 1)
    await _link(db, root, after, 2)
    await _material(db, topic, 1, created_at=LONG_AGO)
    await _task(db, topic, 1, created_at=LONG_AGO)
    await _task(db, topic, 2, created_at=LONG_AGO)
    tail_old = await _task(db, topic, 3, created_at=LONG_AGO)
    later = await _task(db, after, 1, created_at=LONG_AGO)
    student = await _student(db, root)
    await _result(db, student, tail_old, topic, root, at=LONG_AGO + timedelta(days=3))
    await _result(db, student, later, after, root, at=LONG_AGO + timedelta(days=4))
    # Вставка в середину: позиция 2 сдвигает старые задания 2 и 3 вправо.
    await _task(db, topic, 2, created_at=INSERTED)
    await _material(db, topic, 2, created_at=INSERTED)
    return [root], student


async def shape_personal_requirement(db) -> tuple[list[int], int]:
    """Персональная обязательность tsk-1361: решение учителя «рекомендуемое»."""
    root = await _course(db, "личная корень")
    topic = await _course(db, "личная тема")
    await _link(db, root, topic, 1)
    m1 = await _material(db, topic, 1)
    await _material(db, topic, 2)
    t1 = await _task(db, topic, 1)
    await _task(db, topic, 2)
    await _task(db, root, 1)
    student = await _student(db, root)
    teacher = await _student(db)
    for kind, target in (("material", m1), ("task", t1)):
        await set_human_override(
            db, student_id=student, target_kind=kind, target_id=target,
            level="recommended", by_user_id=teacher,
        )
    return [root], student


async def shape_dependencies(db) -> tuple[list[int], int]:
    """Зависимости курсов: корень ждёт другой корень, подраздел ждёт подраздел."""
    first = await _course(db, "зависимость первый")
    second = await _course(db, "зависимость второй")
    s1 = await _course(db, "зависимость второй/1")
    s2 = await _course(db, "зависимость второй/2")
    await _link(db, second, s1, 1)
    await _link(db, second, s2, 2)
    await _task(db, first, 1)
    await _material(db, s1, 1)
    await _task(db, s1, 1)
    await _material(db, s2, 1)
    await _task(db, s2, 1)
    await db.execute(
        text(
            "INSERT INTO course_dependencies (course_id, required_course_id) "
            "VALUES (:a, :b), (:c, :d)"
        ),
        {"a": second, "b": first, "c": s2, "d": s1},
    )
    student = await _student(db, first, second)
    return [first, second], student


SHAPES = {
    "only_materials": shape_only_materials,
    "only_tasks": shape_only_tasks,
    "nested": shape_nested,
    "transparent": shape_transparent,
    "sampling": shape_sampling,
    "grace": shape_grace,
    "personal_requirement": shape_personal_requirement,
    "dependencies": shape_dependencies,
}


# ─── Снимок выдачи ──────────────────────────────────────────────────────────


def _reset_caches(db) -> None:
    """Сбросить кеши сессии: каждый путь обязан считать всё сам."""
    invalidate_course_tree_cache(db)
    invalidate_grace_cache(db)
    db.info.pop("tsk1276_locks", None)


def _next_view(res: Any) -> tuple:
    return (res.type, res.course_id, res.root_course_id, res.material_id, res.task_id,
            res.dependency_course_id)


async def _tree_items(db, roots: list[int]) -> list[tuple[str, int]]:
    """Все материалы и задания деревьев, включая неактивные и рекомендуемые."""
    engine = LearningEngineService()
    ids: set[int] = set()
    for root in roots:
        ids.update(await engine._collect_courses_in_order(db, root))  # noqa: SLF001
    rows = (
        await db.execute(
            text(
                "SELECT 'material', id FROM materials WHERE course_id = ANY(:ids) "
                "UNION ALL SELECT 'task', id FROM tasks WHERE course_id = ANY(:ids) "
                "ORDER BY 1, 2"
            ),
            {"ids": sorted(ids)},
        )
    ).fetchall()
    return [(str(k), int(i)) for k, i in rows]


async def _chain(db, student: int, root: Optional[int]) -> list[tuple]:
    """Цепочка next-item от начала: каждый шаг — после предыдущего элемента."""
    engine = LearningEngineService()
    chain: list[tuple] = []
    res = await engine.resolve_next_item(db, student, root_course_id=root)
    seen: set[tuple] = set()
    while len(chain) < CHAIN_LIMIT:
        chain.append(_next_view(res))
        if res.type not in ("material", "task", "blocked_limit"):
            break
        pos = ("material", res.material_id) if res.type == "material" else ("task", res.task_id)
        if pos in seen:
            chain.append(("ЦИКЛ", pos))
            break
        seen.add(pos)
        res = await engine.resolve_next_item(
            db, student, root_course_id=root,
            after_material_id=pos[1] if pos[0] == "material" else None,
            after_task_id=pos[1] if pos[0] == "task" else None,
        )
    return chain


async def _snapshot(db, roots: list[int], student: int, items: list[tuple[str, int]]) -> dict:
    """Всё, что зависит от порядка узла, — для одного положения рубильника."""
    _reset_caches(db)
    engine = LearningEngineService()
    snap: dict[str, Any] = {}
    for root in roots:
        snap[("courses", root)] = await engine._collect_courses_in_order(db, root)  # noqa: SLF001
        snap[("syllabus", root)] = await me_service.get_syllabus_states(
            db, user_id=student, root_course_id=root
        )
        snap[("chain", root)] = await _chain(db, student, root)
        for kind, item_id in items:
            res = await engine.resolve_next_item(
                db, student, root_course_id=root,
                after_material_id=item_id if kind == "material" else None,
                after_task_id=item_id if kind == "task" else None,
            )
            snap[("after", root, kind, item_id)] = _next_view(res)
            snap[("preview", root, kind, item_id)] = await preview_next_item(
                db, kind, item_id, root_course_id=root
            )
        snap[("progress", root)] = await manual_progress_service.get_student_progress(
            db, student_id=student, course_id=root
        )
        snap[("grace_reasons", root)] = await compute_grace_reasons(db, student, root)
        snap[("graced", root)] = await compute_graced_items(db, student, root)
    snap[("chain", None)] = await _chain(db, student, None)
    return snap


def _diff(a: dict, b: dict) -> list[str]:
    out = []
    for key in sorted(set(a) | set(b), key=repr):
        if a.get(key) != b.get(key):
            out.append(f"{key!r}:\n  true:  {a.get(key)!r}\n  false: {b.get(key)!r}")
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("read_overrides", [False, True], ids=["grace-calc", "grace-table"])
async def test_output_identical_with_switch_on_and_off(db, monkeypatch, shape, read_overrides):
    """Снимок выдачи совпадает при NODE_SEQUENCE_ENABLED=true и false.

    `read_overrides` — второй режим персональной обязательности (tsk-1361 Б2):
    прощённое берётся из таблицы переопределений, а не считается на лету.
    """
    monkeypatch.setenv(
        "REQUIREMENT_OVERRIDE_READ_ENABLED", "true" if read_overrides else "false"
    )
    roots, student = await SHAPES[shape](db)
    await db.commit()
    if read_overrides:
        from app.services.requirement_override_service import sync_rule_overrides

        await sync_rule_overrides(db, student)
        await db.commit()
    items = await _tree_items(db, roots)
    assert items, "форма без элементов ничего не проверяет"

    snaps = []
    for flag in ("true", "false", "true"):
        monkeypatch.setenv("NODE_SEQUENCE_ENABLED", flag)
        snaps.append(await _snapshot(db, roots, student, items))

    on, off, on_again = snaps
    assert not _diff(on, off), "выдача разошлась:\n" + "\n".join(_diff(on, off))
    assert not _diff(on, on_again), "повторный прогон разошёлся:\n" + "\n".join(
        _diff(on, on_again)
    )
    # Цепочка обязана что-то выдавать — иначе сравнивались бы пустые ответы.
    assert any(
        step[0] in ("material", "task", "blocked_limit", "blocked_dependency")
        for root in roots for step in on[("chain", root)]
    ), on


# ─── Число запросов ─────────────────────────────────────────────────────────


async def _count_statements(db, db_engine, call) -> int:
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    _reset_caches(db)
    event.listen(db_engine.sync_engine, "before_cursor_execute", _record)
    try:
        await call()
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", _record)
    return len(statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("position", ["start", "material", "task"])
async def test_next_item_query_count_not_higher(db, db_engine, monkeypatch, position):
    """Один next-item по `node_sequence` стоит не больше запросов, чем прежде.

    Кеш сессии сбрасывается перед каждым замером: сравнивается холодный вызов
    (обход дерева + шаги), то есть худший случай.
    """
    roots, student = await shape_nested(db)
    await db.commit()
    root = roots[0]
    engine = LearningEngineService()
    material_id = int(
        (
            await db.execute(
                text(
                    "SELECT m.id FROM materials m JOIN course_parents cp ON cp.course_id = m.course_id "
                    "WHERE cp.parent_course_id = :r AND m.is_active ORDER BY m.id LIMIT 1"
                ),
                {"r": root},
            )
        ).scalar_one()
    )
    task_id = int(
        (
            await db.execute(
                text("SELECT id FROM tasks WHERE course_id = :r ORDER BY order_position LIMIT 1"),
                {"r": root},
            )
        ).scalar_one()
    )
    kwargs = {
        "start": {},
        "material": {"after_material_id": material_id},
        "task": {"after_task_id": task_id},
    }[position]

    counts = {}
    for flag in ("false", "true"):
        monkeypatch.setenv("NODE_SEQUENCE_ENABLED", flag)
        counts[flag] = await _count_statements(
            db, db_engine,
            lambda: engine.resolve_next_item(db, student, root_course_id=root, **kwargs),
        )
    print(f"tsk996 запросов на next-item ({position}): прежний={counts['false']} новый={counts['true']}")
    assert counts["true"] <= counts["false"], counts


# ─── Сама функция ───────────────────────────────────────────────────────────


def test_node_sequence_head_children_tail():
    """Голова — материалы по позиции, хвост — задания по `task_key`."""
    seq = node_sequence(
        materials=[(11, 2, 1), (10, None, 1), (12, 1, 1)],
        tasks=[(21, task_key(2, 21)), (20, task_key(None, 20)), (22, task_key(1, 22))],
    )
    assert [(e.kind, e.item_id) for e in seq] == [
        ("material", 12), ("material", 11), ("material", 10),
        ("task", 22), ("task", 21), ("task", 20),
    ]


def test_walk_steps_post_order_and_dedup():
    """Обход: голова узла, подразделы по `order_number`, хвост; повтор узла — один раз."""
    children = {1: [(3, 2), (2, 1)], 2: [(4, None)], 3: [(4, 1)], 4: []}
    assert walk_steps(1, lambda c: children.get(c, [])) == [
        (1, HEAD), (2, HEAD), (4, HEAD), (4, TAIL), (2, TAIL), (3, HEAD), (3, TAIL), (1, TAIL),
    ]
