"""tsk-612: переиздание курса не должно стирать название задания.

Названия заданий (`task_content.title`) заводит методист или бэкфилл, а не
источник: конвейеры ContentBackbone (`lms_import_file.py`, `wp_nav_import.py`)
шлют `title=""`, схема приводит пустую строку к None (tsk-107), а UPDATE в
`bulk_upsert` перезаписывает `task_content` целиком. Без защиты ближайший
прогон импорта обнулил бы всю работу по наименованию 6.4 тысяч заданий.

Acceptance:
- AC-1: импорт с пустым названием НЕ стирает заведённое (ядро задачи);
- AC-2: импорт с непустым названием название обновляет (источник, который
  названия умеет — sdamgia — остаётся хозяином своего поля);
- AC-3: остальные поля `task_content` импорт по-прежнему обновляет (защита
  точечная: условие задачи из источника продолжает доезжать);
- AC-4: у задания без названия ничего не выдумывается.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.tasks_service import TasksService, keep_curated_title


_settings = Settings()

_SOLUTION_RULES: dict[str, Any] = {
    "type": "SA",
    "accepted_answers": ["2"],
    "max_score": 1,
}


class _FakeTask:
    """Минимальная замена строки задания для unit-проверок хелпера."""

    def __init__(self, task_content: Any, task_id: int = 1) -> None:
        self.task_content = task_content
        self.id = task_id


# ---------- unit: сам инвариант ----------


@pytest.mark.parametrize("blank", [None, "", "   ", 42, {"a": 1}])
def test_blank_incoming_title_keeps_existing(blank: Any) -> None:
    """AC-1: любое «пусто» на входе не затирает заведённое название."""
    existing = _FakeTask({"type": "SA", "stem": "старое", "title": "Остаток от деления"})
    incoming = {"type": "SA", "stem": "новое", "title": blank}
    assert keep_curated_title(incoming, existing)["title"] == "Остаток от деления"


def test_missing_title_key_keeps_existing() -> None:
    """AC-1: ключа title вообще нет — это тоже молчание источника."""
    existing = _FakeTask({"type": "SA", "stem": "старое", "title": "Скорость по пути"})
    assert keep_curated_title({"type": "SA", "stem": "новое"}, existing)["title"] == (
        "Скорость по пути"
    )


def test_incoming_title_wins() -> None:
    """AC-2: источник, который названия умеет, своё поле не теряет."""
    existing = _FakeTask({"type": "SA", "stem": "старое", "title": "Старое имя"})
    incoming = {"type": "SA", "stem": "новое", "title": "ОГЭ. Задание 13 — вариант 10593"}
    assert keep_curated_title(incoming, existing)["title"] == (
        "ОГЭ. Задание 13 — вариант 10593"
    )


@pytest.mark.parametrize("existing_title", [None, "", "  "])
def test_nothing_invented_when_both_empty(existing_title: Any) -> None:
    """AC-4: пусто с обеих сторон — название не выдумывается."""
    existing = _FakeTask({"type": "SA", "stem": "старое", "title": existing_title})
    incoming = {"type": "SA", "stem": "новое"}
    result = keep_curated_title(incoming, existing)
    assert result.get("title") in (None, "", "  ")


def test_helper_does_not_mutate_incoming() -> None:
    """Хелпер возвращает новый словарь: импортный payload переиспользуется выше."""
    existing = _FakeTask({"type": "SA", "stem": "старое", "title": "Имя"})
    incoming = {"type": "SA", "stem": "новое"}
    keep_curated_title(incoming, existing)
    assert "title" not in incoming


# ---------- integration: bulk_upsert e2e ----------


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_tsk612_course():
    """`TasksService.bulk_upsert` коммитит — нужен пост-test cleanup."""
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = 'test_tsk612'"
                        ")"
                    )
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = 'test_tsk612'")
                )
    finally:
        await engine.dispose()


async def _new_course(db: AsyncSession) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_tsk612', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _read_content(db: AsyncSession, task_id: int) -> dict[str, Any]:
    row = (
        await db.execute(
            text("SELECT task_content FROM tasks WHERE id = :tid"), {"tid": task_id}
        )
    ).first()
    return row.task_content


@pytest.mark.asyncio
async def test_reimport_keeps_curated_title(db: AsyncSession):
    """AC-1 + AC-3: переиздание сохраняет название и обновляет условие."""
    course_id = await _new_course(db)
    service = TasksService()

    base = {
        "external_uid": "TSK612-1",
        "course_id": course_id,
        "difficulty_id": 1,
        "solution_rules": _SOLUTION_RULES,
        "max_score": 1,
    }
    created = await service.bulk_upsert(
        db,
        [{**base, "task_content": {"type": "SA", "stem": "исходное условие", "title": "Остаток от деления 17 на 5"}}],
    )
    assert created[0][1] == "created"
    task_id = created[0][2]

    # Повторный прогон конвейера: названия он не знает и шлёт пустое.
    updated = await service.bulk_upsert(
        db,
        [{**base, "task_content": {"type": "SA", "stem": "обновлённое условие", "title": ""}}],
    )
    assert updated[0][1] == "updated"

    content = await _read_content(db, task_id)
    assert content["title"] == "Остаток от деления 17 на 5"
    assert content["stem"] == "обновлённое условие"


@pytest.mark.asyncio
async def test_reimport_applies_source_title(db: AsyncSession):
    """AC-2: непустое название из источника перезаписывает старое."""
    course_id = await _new_course(db)
    service = TasksService()

    base = {
        "external_uid": "TSK612-2",
        "course_id": course_id,
        "difficulty_id": 1,
        "solution_rules": _SOLUTION_RULES,
        "max_score": 1,
    }
    created = await service.bulk_upsert(
        db, [{**base, "task_content": {"type": "SA", "stem": "stem", "title": "Старое имя"}}]
    )
    task_id = created[0][2]

    await service.bulk_upsert(
        db, [{**base, "task_content": {"type": "SA", "stem": "stem", "title": "Новое имя"}}]
    )

    content = await _read_content(db, task_id)
    assert content["title"] == "Новое имя"
