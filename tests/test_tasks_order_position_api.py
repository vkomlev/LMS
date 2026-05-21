"""ТЗ-2 (фаза 2): тесты API расширения tasks.order_position.

Покрывает кейсы T16-T19 (bulk_upsert) и T20-T22 (schemas/order)
из тест-плана `docs/briefs/tsk-004-tasks-order-position-testplan.md`.

Стратегия: тесты вызывают `TasksService` напрямую (HTTP-роутер CRUD generic
и проходит через тот же сервис, поэтому покрытие сервисом достаточно для smoke).
Каждый тест создаёт временный курс и работает в session-scoped транзакции (rollback).
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.schemas.tasks import (
    TaskCreate,
    TaskRead,
    TaskUpdate,
    TaskUpsertItem,
)
from app.services.tasks_service import TasksService


_settings = Settings()


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_test_op_api_courses():
    """`TasksService.bulk_upsert` через BaseRepository делает COMMIT внутри,
    поэтому `db.rollback()` не очищает данные. Autouse-фикстура удаляет
    тестовые курсы с title='test_op_api' и каскадом — все их задачи —
    после каждого теста.
    """
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = 'test_op_api'"
                        ")"
                    )
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = 'test_op_api'")
                )
    finally:
        await engine.dispose()


_TASK_CONTENT: dict[str, Any] = {
    "type": "SC",
    "stem": "test",
    "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
}
_SOLUTION_RULES: dict[str, Any] = {"type": "SC", "correct_options": ["a"], "max_score": 1}


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_op_api', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _positions(db, course_id: int) -> list[tuple[int, int | None]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, order_position
                FROM tasks WHERE course_id = :cid
                ORDER BY order_position NULLS LAST, id
                """
            ),
            {"cid": course_id},
        )
    ).all()
    return [(int(r.id), int(r.order_position) if r.order_position is not None else None) for r in rows]


# ---------- Schemas ----------


def test_task_create_accepts_order_position():
    """TaskCreate принимает Optional order_position."""
    m = TaskCreate(
        task_content=_TASK_CONTENT,
        course_id=1,
        difficulty_id=1,
        solution_rules=_SOLUTION_RULES,
        order_position=5,
    )
    assert m.order_position == 5

    m2 = TaskCreate(task_content={}, course_id=1, difficulty_id=1)
    assert m2.order_position is None


def test_task_update_order_position_optional():
    """TaskUpdate.order_position по умолчанию None (поле не передано)."""
    m = TaskUpdate(order_position=3)
    assert m.order_position == 3

    m2 = TaskUpdate()
    assert m2.order_position is None


def test_task_read_includes_order_position():
    """TaskRead отдаёт поле order_position."""
    # эмулируем ORM-объект через dict (TaskRead использует from_attributes)
    payload = {
        "id": 1,
        "task_content": _TASK_CONTENT,
        "course_id": 1,
        "difficulty_id": 1,
        "solution_rules": _SOLUTION_RULES,
        "max_score": 1,
        "external_uid": "x",
        "order_position": 7,
    }
    read = TaskRead.model_validate(payload)
    assert read.order_position == 7


def test_task_upsert_item_includes_order_position():
    """TaskUpsertItem допускает order_position int|None."""
    item = TaskUpsertItem(
        external_uid="X-1",
        course_id=1,
        difficulty_id=1,
        task_content=_TASK_CONTENT,
        solution_rules=_SOLUTION_RULES,
        order_position=2,
    )
    assert item.order_position == 2

    item2 = TaskUpsertItem(
        external_uid="X-2",
        course_id=1,
        difficulty_id=1,
        task_content=_TASK_CONTENT,
        solution_rules=_SOLUTION_RULES,
    )
    assert item2.order_position is None


# ---------- bulk_upsert (T16-T19) ----------


@pytest.mark.asyncio
async def test_t16_bulk_upsert_create_explicit_position(db):
    """T16. bulk_upsert CREATE с явным order_position сдвигает остальных."""
    course_id = await _new_course(db)
    service = TasksService()

    # Сначала создаём 3 задачи без позиции (через bulk)
    init = [
        {
            "external_uid": f"T16-{i}",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": _TASK_CONTENT,
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
        for i in range(3)
    ]
    await service.bulk_upsert(db, init)

    # Теперь вставляем 4-ю явно на позицию 2
    res = await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T16-X",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                "order_position": 2,
            }
        ],
    )
    new_id = res[0][2]

    rows = await _positions(db, course_id)
    # Проверяем что новая задача встала на 2, остальные сдвинулись
    pos_of_new = next(p for (i, p) in rows if i == new_id)
    assert pos_of_new == 2
    assert len(rows) == 4
    # Позиции 1..4 без дырок
    assert [p for _, p in rows] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_t17_bulk_upsert_create_null_position(db):
    """T17. bulk_upsert CREATE без order_position → триггер MAX+1."""
    course_id = await _new_course(db)
    service = TasksService()

    res = await service.bulk_upsert(
        db,
        [
            {
                "external_uid": f"T17-{i}",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
            for i in range(3)
        ],
    )
    rows = await _positions(db, course_id)
    assert [p for _, p in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_t18_bulk_upsert_update_changes_position(db):
    """T18. bulk_upsert UPDATE с новым order_position перемещает задачу."""
    course_id = await _new_course(db)
    service = TasksService()

    init = [
        {
            "external_uid": f"T18-{i}",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": _TASK_CONTENT,
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
        for i in range(3)
    ]
    await service.bulk_upsert(db, init)

    # UPDATE T18-2 c order_position=1 (переместить с 3 на 1)
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T18-2",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                "order_position": 1,
            }
        ],
    )

    rows = await _positions(db, course_id)
    # T18-2 теперь на позиции 1, остальные сдвинулись
    external_uid_at_pos1 = (
        await db.execute(
            text(
                "SELECT external_uid FROM tasks WHERE course_id = :cid AND order_position = 1"
            ),
            {"cid": course_id},
        )
    ).scalar()
    assert external_uid_at_pos1 == "T18-2"
    assert [p for _, p in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_t19_bulk_upsert_update_without_position_preserves(db):
    """T19. bulk_upsert UPDATE без order_position — позиция НЕ меняется.

    Критический контракт: None в payload означает «поле не передано»,
    а не «обнулить позицию»."""
    course_id = await _new_course(db)
    service = TasksService()

    init = [
        {
            "external_uid": f"T19-{i}",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": _TASK_CONTENT,
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
        for i in range(3)
    ]
    await service.bulk_upsert(db, init)

    # Запоминаем позицию T19-1
    pos_before = (
        await db.execute(
            text("SELECT order_position FROM tasks WHERE external_uid = :u"),
            {"u": "T19-1"},
        )
    ).scalar()
    assert pos_before == 2

    # UPDATE T19-1 БЕЗ order_position (идемпотентный re-upsert тех же полей)
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T19-1",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                # order_position намеренно не передан
            }
        ],
    )

    # Позиция должна остаться 2
    pos_after = (
        await db.execute(
            text("SELECT order_position FROM tasks WHERE external_uid = :u"),
            {"u": "T19-1"},
        )
    ).scalar()
    assert pos_after == 2, f"order_position не должен меняться, было {pos_before}, стало {pos_after}"


# ---------- get_by_course (T20) ----------


@pytest.mark.asyncio
async def test_t20_get_by_course_returns_ordered_by_order_position(db):
    """T20. TasksService.get_by_course возвращает в порядке order_position NULLS LAST, id."""
    course_id = await _new_course(db)
    service = TasksService()

    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": f"T20-{i}",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
            for i in range(4)
        ],
    )

    # Переставляем порядок: T20-3 на позицию 1
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T20-3",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _TASK_CONTENT,
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                "order_position": 1,
            }
        ],
    )

    items, total = await service.get_by_course(db, course_id=course_id, limit=100, offset=0)
    assert total == 4
    # Первая задача в выдаче должна быть та, что на позиции 1 = T20-3
    assert items[0].external_uid == "T20-3"
    # Порядок строго возрастающий по order_position
    positions = [t.order_position for t in items]
    assert positions == sorted(positions)
