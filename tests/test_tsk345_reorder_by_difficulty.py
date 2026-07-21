"""tsk-345: durable-фикс — bulk_upsert больше не ломает межгрупповой порядок
THEORY→EASY→NORMAL→HARD→PROJECT при новых импортах и переклассификации.

Корневая причина: `trg_set_task_order_position` при CREATE без явного
order_position ставит MAX+1 (в конец курса) независимо от difficulty_id/type,
а UPDATE без явного order_position не трогает позицию вовсе — переклассификация
задания (напр. THEORY-перетег tsk-318) оставляет его в чужой группе.

Фикс: `TasksService.bulk_upsert` отслеживает курсы, где CREATE получил
order_position через авто-триггер либо UPDATE сменил difficulty_id/type без
явной позиции, и в конце батча пересортировывает эти курсы через
`_reorder_tasks_by_difficulty` (тот же ROW_NUMBER-паттерн, что и
scripts/reorder_tasks_by_difficulty_type.py, миграция этапа 1.7).

Стратегия: `TasksService.bulk_upsert` коммитит внутри (BaseRepository create/
update commit=True) — `db` fixture savepoint-rollback это не откатывает.
Зеркало test_tasks_reorder_api.py: autouse cleanup по title='test_tsk345_reorder'.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.tasks_service import TasksService

_settings = Settings()

_SOLUTION_RULES: dict[str, Any] = {"type": "SC", "correct_options": ["a"], "max_score": 1}


def _task_content(task_type: str = "SA_COM") -> dict[str, Any]:
    if task_type == "SA_COM":
        return {"type": "SA_COM", "stem": "x", "accepted_answers": ["1"]}
    return {
        "type": task_type,
        "stem": "x",
        "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
    }


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_test_tsk345_courses():
    """bulk_upsert коммитит внутри — чистим тестовые курсы после каждого теста."""
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = 'test_tsk345_reorder'"
                        ")"
                    )
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = 'test_tsk345_reorder'")
                )
    finally:
        await engine.dispose()


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_tsk345_reorder', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _ordered_uids(db, course_id: int) -> list[str]:
    rows = (
        await db.execute(
            text(
                """
                SELECT external_uid FROM tasks
                WHERE course_id = :cid
                ORDER BY order_position NULLS LAST, id
                """
            ),
            {"cid": course_id},
        )
    ).all()
    return [r.external_uid for r in rows]


@pytest.mark.asyncio
async def test_new_import_after_established_order_stays_sorted(db):
    """Регресс tsk-345: НОВОЕ задание, добавленное ПОСЛЕ того как курс уже
    отсортирован, не должно уезжать в конец курса, если его сложность ниже —
    ровно так задание 2059 (NORMAL) осталось перед EASY-заданиями."""
    course_id = await _new_course(db)
    service = TasksService()

    # Курс уже отсортирован: THEORY, затем NORMAL (имитирует состояние после
    # разового reorder-скрипта миграции этапа 1.7).
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345-THEORY",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            },
            {
                "external_uid": "T345-NORMAL",
                "course_id": course_id,
                "difficulty_id": 3,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            },
        ],
    )
    assert await _ordered_uids(db, course_id) == ["T345-THEORY", "T345-NORMAL"]

    # Новый импорт добавляет EASY-задание БЕЗ order_position (как реальные
    # импорты KompEGE/Крылов/sdamgia после 2026-05-21) — триггер поставил бы
    # его в конец (после NORMAL), что и было живым дефектом.
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345-EASY",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
        ],
    )

    assert await _ordered_uids(db, course_id) == [
        "T345-THEORY",
        "T345-EASY",
        "T345-NORMAL",
    ]


@pytest.mark.asyncio
async def test_reclassify_without_position_reorders(db):
    """Регресс tsk-318: переклассификация difficulty_id через UPDATE без явного
    order_position (тот же паттерн, что THEORY-перетег 135 заданий) не должна
    оставлять задание в чужой группе."""
    course_id = await _new_course(db)
    service = TasksService()

    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345R-EASY-1",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            },
            {
                "external_uid": "T345R-EASY-2",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            },
        ],
    )
    assert await _ordered_uids(db, course_id) == ["T345R-EASY-1", "T345R-EASY-2"]

    # Перетегируем первое задание в THEORY, order_position НЕ передаём —
    # ровно паттерн tsk-318.
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345R-EASY-1",
                "course_id": course_id,
                "difficulty_id": 1,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
        ],
    )

    # THEORY (1) должно стоять перед оставшимся EASY (2).
    assert await _ordered_uids(db, course_id) == ["T345R-EASY-1", "T345R-EASY-2"]
    theory_difficulty = (
        await db.execute(
            text("SELECT difficulty_id FROM tasks WHERE external_uid = :u"),
            {"u": "T345R-EASY-1"},
        )
    ).scalar()
    assert theory_difficulty == 1


@pytest.mark.asyncio
async def test_explicit_position_not_reordered_around(db):
    """CREATE с явным order_position — намерение вызывающего, реордер по
    сложности его не трогает (сохраняем контракт T16 из tsk-004)."""
    course_id = await _new_course(db)
    service = TasksService()

    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345E-NORMAL",
                "course_id": course_id,
                "difficulty_id": 3,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
        ],
    )
    # EASY-задание явно ставим на позицию 1 (перед NORMAL) — валидный явный запрос.
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345E-EASY",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                "order_position": 1,
            }
        ],
    )

    assert await _ordered_uids(db, course_id) == ["T345E-EASY", "T345E-NORMAL"]


@pytest.mark.asyncio
async def test_trigger_stays_active_after_bulk_upsert_reorder(db):
    """Реордер временно выключает триггеры на UPDATE — после batch они должны
    остаться включёнными (зеркало BR6 test_tasks_reorder_api.py)."""
    course_id = await _new_course(db)
    service = TasksService()

    # CREATE без order_position → внутренний реордер сработает.
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345TRG-1",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
            }
        ],
    )

    trigger_enabled = (
        await db.execute(
            text(
                """
                SELECT tgenabled FROM pg_trigger
                WHERE tgname = 'trg_set_task_order_position'
                """
            )
        )
    ).scalar()
    assert trigger_enabled in ("O", b"O"), "триггер должен остаться включённым (tgenabled='O')"

    # Живая проверка: GUC-флаг реордера не «протёк» за пределы вызова —
    # следующий INSERT в ЭТОМ ЖЕ (незакоммиченном во внешней транзакции теста)
    # соединении по-прежнему обрабатывается триггером, а не молча игнорируется.
    skip_flag = (
        await db.execute(text("SELECT current_setting('app.skip_task_order_trigger', true)"))
    ).scalar()
    assert skip_flag in (None, "false"), (
        f"app.skip_task_order_trigger должен быть сброшен после реордера, а не '{skip_flag}'"
    )

    # Живая проверка: новый CREATE с явной позицией всё ещё сдвигает соседей
    # силами триггера (а не силами реордера, для этого курса не нужного).
    await service.bulk_upsert(
        db,
        [
            {
                "external_uid": "T345TRG-2",
                "course_id": course_id,
                "difficulty_id": 2,
                "task_content": _task_content(),
                "solution_rules": _SOLUTION_RULES,
                "max_score": 1,
                "order_position": 1,
            }
        ],
    )
    assert await _ordered_uids(db, course_id) == ["T345TRG-2", "T345TRG-1"]
