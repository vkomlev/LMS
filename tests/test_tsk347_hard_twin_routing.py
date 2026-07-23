"""tsk-347: durable-инвариант — импорт не утаскивает HARD обратно в номерной курс.

Сложные задания ЕГЭ вынесены в отдельный необязательный блок в конце программы
(подкурс на каждый номер, `courses.course_uid = 'lms:tsk347:hard:<course_id>'`).
Сам перенос — правка данных, но `TasksService.bulk_upsert` при UPDATE
перезаписывает `course_id` и `requirement_level` значениями из payload, а все
конвейеры (ContentBackbone, Google Sheets, прямой bulk-upsert) шлют номерной
курс и уровень по умолчанию `required`. Без инварианта в коде первая же доливка
KompEGE/Крылова вернула бы задания в основной поток — тот же класс регрессии,
который tsk-345 чинил для порядка.

Проверяется: HARD уезжает в подкурс сложных и становится `recommended`;
не-HARD в том же батче остаётся в номерном курсе; курс без подкурса сложных
ведёт себя как раньше (регресс).

Стратегия та же, что у test_tsk345_reorder_by_difficulty: `bulk_upsert`
коммитит внутри (BaseRepository create/update commit=True), поэтому savepoint
фикстуры `db` его не откатывает — чистим по title автоюз-фикстурой.
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

_TITLE = "test_tsk347_hard_twin"
_SOLUTION_RULES: dict[str, Any] = {"type": "SC", "correct_options": ["a"], "max_score": 1}

HARD = 4
NORMAL = 3


def _task_content() -> dict[str, Any]:
    return {"type": "SA_COM", "stem": "x", "accepted_answers": ["1"]}


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup():
    """bulk_upsert коммитит внутри — чистим тестовые курсы после каждого теста."""
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = :t)"
                    ),
                    {"t": _TITLE},
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = :t"), {"t": _TITLE}
                )
    finally:
        await engine.dispose()


async def _new_course(db, course_uid: str | None = None) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES (:t, 'test', 'self_guided', false, :uid) RETURNING id"
            ),
            {"t": _TITLE, "uid": course_uid},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _task_row(db, external_uid: str) -> tuple[int, str]:
    row = (
        await db.execute(
            text("SELECT course_id, requirement_level FROM tasks WHERE external_uid = :uid"),
            {"uid": external_uid},
        )
    ).first()
    return int(row.course_id), row.requirement_level


def _payload(external_uid: str, course_id: int, difficulty_id: int) -> dict[str, Any]:
    """Payload импорта: уровень обязательности НЕ передаётся — как у конвейеров."""
    return {
        "external_uid": external_uid,
        "course_id": course_id,
        "difficulty_id": difficulty_id,
        "task_content": _task_content(),
        "solution_rules": _SOLUTION_RULES,
        "max_score": 1,
    }


@pytest.mark.asyncio
async def test_hard_import_routed_to_twin_subcourse(db):
    """Новое HARD-задание с импорта уезжает в подкурс сложных и становится recommended."""
    src_id = await _new_course(db)
    twin_id = await _new_course(db, course_uid=f"lms:tsk347:hard:{src_id}")
    service = TasksService()

    await service.bulk_upsert(db, [_payload("T347-HARD-1", src_id, HARD)])

    course_id, requirement_level = await _task_row(db, "T347-HARD-1")
    assert course_id == twin_id
    assert requirement_level == "recommended"


@pytest.mark.asyncio
async def test_reimport_does_not_drag_hard_back(db):
    """Главный регресс: повторный импорт того же задания на номерной курс
    (payload шлёт course_id номерного и уровень по умолчанию) НЕ возвращает его
    в основной поток."""
    src_id = await _new_course(db)
    twin_id = await _new_course(db, course_uid=f"lms:tsk347:hard:{src_id}")
    service = TasksService()

    await service.bulk_upsert(db, [_payload("T347-HARD-2", src_id, HARD)])
    # Доливка тем же конвейером — ровно как KompEGE/Крылов после переноса.
    await service.bulk_upsert(db, [_payload("T347-HARD-2", src_id, HARD)])

    course_id, requirement_level = await _task_row(db, "T347-HARD-2")
    assert course_id == twin_id
    assert requirement_level == "recommended"


@pytest.mark.asyncio
async def test_non_hard_stays_in_source_course(db):
    """Не-HARD задание того же батча остаётся в номерном курсе и обязательным."""
    src_id = await _new_course(db)
    await _new_course(db, course_uid=f"lms:tsk347:hard:{src_id}")
    service = TasksService()

    await service.bulk_upsert(
        db,
        [_payload("T347-HARD-3", src_id, HARD), _payload("T347-NORMAL-1", src_id, NORMAL)],
    )

    course_id, requirement_level = await _task_row(db, "T347-NORMAL-1")
    assert course_id == src_id
    assert requirement_level == "required"


@pytest.mark.asyncio
async def test_downgrade_from_hard_returns_to_source(db):
    """Переклассификация HARD -> NORMAL возвращает задание в номерной курс:
    маршрутизация идёт по сложности, а не по факту «однажды уехало»."""
    src_id = await _new_course(db)
    twin_id = await _new_course(db, course_uid=f"lms:tsk347:hard:{src_id}")
    service = TasksService()

    await service.bulk_upsert(db, [_payload("T347-HARD-4", src_id, HARD)])
    assert (await _task_row(db, "T347-HARD-4"))[0] == twin_id

    await service.bulk_upsert(db, [_payload("T347-HARD-4", src_id, NORMAL)])

    course_id, requirement_level = await _task_row(db, "T347-HARD-4")
    assert course_id == src_id
    assert requirement_level == "required"


@pytest.mark.asyncio
async def test_reissue_into_twin_keeps_recommended(db):
    """Round-trip «переиздание» (ContentBackbone lms_stem_hygiene читает задание
    уже из подкурса сложных и шлёт обратно с тем же course_id) не должно
    вернуть заданию `required`: у payload-моделей конвейеров поля уровня нет
    вовсе, а API подставляет дефолт `required`."""
    src_id = await _new_course(db)
    twin_id = await _new_course(db, course_uid=f"lms:tsk347:hard:{src_id}")
    service = TasksService()

    await service.bulk_upsert(db, [_payload("T347-HARD-6", src_id, HARD)])
    # Переиздание: course_id уже подкурса сложных, уровень не передан.
    await service.bulk_upsert(db, [_payload("T347-HARD-6", twin_id, HARD)])

    course_id, requirement_level = await _task_row(db, "T347-HARD-6")
    assert course_id == twin_id
    assert requirement_level == "recommended"


@pytest.mark.asyncio
async def test_course_without_twin_unchanged(db):
    """Регресс: у курса без подкурса сложных поведение прежнее — HARD остаётся
    в самом курсе с уровнем из payload."""
    src_id = await _new_course(db)
    service = TasksService()

    await service.bulk_upsert(db, [_payload("T347-HARD-5", src_id, HARD)])

    course_id, requirement_level = await _task_row(db, "T347-HARD-5")
    assert course_id == src_id
    assert requirement_level == "required"
