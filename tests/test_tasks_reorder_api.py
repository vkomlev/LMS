"""ТЗ tsk-004 этап 1.7 (Root: 1.8): bulk reorder endpoint для tasks.

Покрывает кейсы BR1-BR11 из
``docs/specs/2026-05-21-tz-tasks-bulk-reorder-stage1-7.md``.

Стратегия:
- ``TasksRepository.reorder_tasks`` делает ``await db.commit()`` внутри —
  `db` fixture rollback не очистит. Используем autouse cleanup-фикстуру
  (зеркало test_tasks_order_position_api.py): удаляет тестовые курсы
  с title='test_reorder' и каскадно — все их задачи.
- HTTP-роутер тонкий и пробрасывает в сервис; кейсы проверяем на уровне сервиса.
  Только BR11 (Pydantic Field ge=1) тестируется на уровне схемы.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.schemas.tasks import TaskOrderItem, TaskReorderRequest
from app.services.tasks_service import TasksService
from app.utils.exceptions import DomainError


_settings = Settings()
_TASK_CONTENT: dict[str, Any] = {
    "type": "SC",
    "stem": "x",
    "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
}
_SOLUTION_RULES: dict[str, Any] = {"type": "SC", "correct_options": ["a"], "max_score": 1}


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_test_reorder_courses():
    """`TasksRepository.reorder_tasks` через BaseRepository делает COMMIT,
    `db.rollback()` не достаточен. Чистим тестовые курсы title='test_reorder'
    после каждого теста, каскад снимает связанные задачи."""
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = 'test_reorder'"
                        ")"
                    )
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = 'test_reorder'")
                )
    finally:
        await engine.dispose()


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_reorder', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.commit()
    return int(row.id)


async def _make_tasks(db, course_id: int, n: int, offset: int = 0) -> list[int]:
    """Создать N задач в курсе через bulk_upsert. Возвращает их id в порядке создания.

    ``offset`` сдвигает external_uid-нумерацию для случаев, когда тест делает
    несколько вызовов в один курс и не должен попасть в UPDATE по тому же uid.
    """
    service = TasksService()
    items = [
        {
            "external_uid": f"BR-{course_id}-{offset + i}",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": _TASK_CONTENT,
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
        for i in range(n)
    ]
    res = await service.bulk_upsert(db, items)
    return [r[2] for r in res]


async def _positions(db, course_id: int) -> dict[int, int | None]:
    rows = (
        await db.execute(
            text("SELECT id, order_position FROM tasks WHERE course_id = :cid"),
            {"cid": course_id},
        )
    ).all()
    return {int(r.id): (int(r.order_position) if r.order_position is not None else None) for r in rows}


# ---------- BR1: полный reorder ----------


@pytest.mark.asyncio
async def test_br1_full_reorder(db):
    """BR1. Полный reorder 5 заданий в новом порядке → 200, updated=5."""
    course_id = await _new_course(db)
    ids = await _make_tasks(db, course_id, 5)  # позиции 1..5

    service = TasksService()
    # Обратим порядок: ids[0]→5, ids[1]→4, ids[2]→3, ids[3]→2, ids[4]→1
    new_order = [
        {"task_id": ids[i], "order_position": 5 - i} for i in range(5)
    ]
    result = await service.reorder_tasks(db, course_id, new_order)
    assert len(result) == 5

    positions = await _positions(db, course_id)
    assert positions[ids[0]] == 5
    assert positions[ids[4]] == 1


# ---------- BR2: task_id не из курса ----------


@pytest.mark.asyncio
async def test_br2_task_not_in_course_rejects_atomic(db):
    """BR2. 1 task_id не принадлежит курсу → 400, состояние не меняется."""
    course_id_a = await _new_course(db)
    course_id_b = await _new_course(db)
    ids_a = await _make_tasks(db, course_id_a, 3)
    ids_b = await _make_tasks(db, course_id_b, 2)

    before = await _positions(db, course_id_a)
    service = TasksService()
    # ids_b[0] не принадлежит курсу A
    new_order = [
        {"task_id": ids_a[0], "order_position": 2},
        {"task_id": ids_b[0], "order_position": 1},
    ]
    with pytest.raises(DomainError) as exc_info:
        await service.reorder_tasks(db, course_id_a, new_order)
    assert exc_info.value.status_code == 400

    after = await _positions(db, course_id_a)
    assert after == before


# ---------- BR3: дубликат task_id ----------


@pytest.mark.asyncio
async def test_br3_duplicate_task_id(db):
    """BR3. Дубликат task_id в теле → 422."""
    course_id = await _new_course(db)
    ids = await _make_tasks(db, course_id, 3)
    before = await _positions(db, course_id)

    service = TasksService()
    new_order = [
        {"task_id": ids[0], "order_position": 1},
        {"task_id": ids[0], "order_position": 2},  # дубликат
    ]
    with pytest.raises(DomainError) as exc_info:
        await service.reorder_tasks(db, course_id, new_order)
    assert exc_info.value.status_code == 422
    assert "task_id" in str(exc_info.value.detail)

    after = await _positions(db, course_id)
    assert after == before


# ---------- BR4: дубликат order_position ----------


@pytest.mark.asyncio
async def test_br4_duplicate_order_position(db):
    """BR4. Дубликат order_position в теле → 422."""
    course_id = await _new_course(db)
    ids = await _make_tasks(db, course_id, 3)
    before = await _positions(db, course_id)

    service = TasksService()
    new_order = [
        {"task_id": ids[0], "order_position": 1},
        {"task_id": ids[1], "order_position": 1},  # дубликат позиции
    ]
    with pytest.raises(DomainError) as exc_info:
        await service.reorder_tasks(db, course_id, new_order)
    assert exc_info.value.status_code == 422
    assert "order_position" in str(exc_info.value.detail)

    after = await _positions(db, course_id)
    assert after == before


# ---------- BR6: триггер активен после reorder ----------


@pytest.mark.asyncio
async def test_br6_trigger_active_after_reorder(db):
    """BR6. После reorder триггер `trg_set_task_order_position` снова активен:
    INSERT без order_position → MAX+1."""
    course_id = await _new_course(db)
    ids = await _make_tasks(db, course_id, 3)

    service = TasksService()
    await service.reorder_tasks(
        db, course_id,
        [{"task_id": ids[i], "order_position": 3 - i} for i in range(3)],
    )

    # Создаём новую задачу без явной позиции — триггер должен поставить MAX+1=4.
    # offset=100 чтобы external_uid не пересёкся с уже созданными.
    new_ids = await _make_tasks(db, course_id, 1, offset=100)
    new_id = new_ids[0]
    positions = await _positions(db, course_id)
    assert positions[new_id] == 4, (
        f"После reorder триггер не активен: новая задача получила "
        f"order_position={positions[new_id]} (ожидалось 4)"
    )


# ---------- BR7: partial reorder ----------


@pytest.mark.asyncio
async def test_br7_partial_reorder(db):
    """BR7. Partial reorder (2 из 4 заданий): перечисленные получают новые позиции,
    остальные сохраняют старые."""
    course_id = await _new_course(db)
    ids = await _make_tasks(db, course_id, 4)
    # Позиции 1,2,3,4. Перенесём только ids[0] на 4, ids[3] на 1.
    before = await _positions(db, course_id)
    assert before[ids[0]] == 1 and before[ids[3]] == 4

    service = TasksService()
    new_order = [
        {"task_id": ids[0], "order_position": 4},
        {"task_id": ids[3], "order_position": 1},
    ]
    await service.reorder_tasks(db, course_id, new_order)

    after = await _positions(db, course_id)
    assert after[ids[0]] == 4
    assert after[ids[3]] == 1
    # Остальные сохранили свои позиции
    assert after[ids[1]] == before[ids[1]]
    assert after[ids[2]] == before[ids[2]]


# ---------- BR9: пустой task_orders ----------


@pytest.mark.asyncio
async def test_br9_empty_task_orders(db):
    """BR9. Пустой `task_orders: []` → 200, updated=0, tasks=[]."""
    course_id = await _new_course(db)
    await _make_tasks(db, course_id, 2)
    before = await _positions(db, course_id)

    service = TasksService()
    result = await service.reorder_tasks(db, course_id, [])
    assert result == []

    after = await _positions(db, course_id)
    assert after == before


# ---------- BR10: course_id не существует ----------


@pytest.mark.asyncio
async def test_br10_course_not_found(db):
    """BR10. course_id не существует → 404."""
    service = TasksService()
    new_order = [{"task_id": 1, "order_position": 1}]
    with pytest.raises(DomainError) as exc_info:
        await service.reorder_tasks(db, 999_999_999, new_order)
    assert exc_info.value.status_code == 404


# ---------- BR11: отрицательная order_position (Pydantic) ----------


def test_br11_negative_order_position_rejected_by_pydantic():
    """BR11. order_position < 1 отклоняется Pydantic Field(ge=1) → ValidationError."""
    with pytest.raises(ValidationError):
        TaskOrderItem(task_id=1, order_position=0)
    with pytest.raises(ValidationError):
        TaskOrderItem(task_id=1, order_position=-5)
    # Sanity: 1 принимается
    m = TaskOrderItem(task_id=1, order_position=1)
    assert m.order_position == 1


def test_br11_reorder_request_validates_items():
    """BR11 sanity: TaskReorderRequest валидирует вложенные TaskOrderItem."""
    with pytest.raises(ValidationError):
        TaskReorderRequest(
            task_orders=[{"task_id": 1, "order_position": 0}]
        )
    m = TaskReorderRequest(
        task_orders=[{"task_id": 1, "order_position": 1}]
    )
    assert len(m.task_orders) == 1
