"""tsk-088: TaskContent schema должен сохранять hints_text/hints_video/has_hints.

Регрессия: до фикса Pydantic v2 default `extra='ignore'` silently отбрасывал
неизвестные поля при `TaskContent.model_validate(...)`. В `tasks_service` это
приводило к тому, что `task_content_obj.model_dump()` возвращал JSON без
hints, и `bulk_upsert` / `create` / `update` записывали в `tasks.task_content`
урезанную версию — VK-разборы Виктора, привязанные к pilot tasks tsk-004
Phase 6.6, не сохранялись в LMS.

Acceptance:
- AC-1: TaskContent.model_validate/model_dump round-trip сохраняет hints.
- AC-2: TaskContent без hints → дефолты `[], [], False` (backward compat).
- AC-4: bulk_upsert с hints_video → задача в БД имеет hints_video в jsonb.
- AC-3: TaskRead.has_hints корректно derive после round-trip через сервис.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.schemas.task_content import TaskContent
from app.schemas.tasks import TaskRead
from app.services.tasks_service import TasksService


_settings = Settings()


# ---------- unit-tests (schema) ----------


def test_task_content_preserves_hints_round_trip():
    """AC-1: model_validate → model_dump сохраняет hints_text/hints_video/has_hints."""
    payload: dict[str, Any] = {
        "type": "SA",
        "stem": "Что выведет print(1+1)?",
        "hints_text": ["Подумай о сложении"],
        "hints_video": ["https://vk.com/video-1_2"],
        "has_hints": True,
    }
    tc = TaskContent.model_validate(payload)
    assert tc.hints_text == ["Подумай о сложении"]
    assert tc.hints_video == ["https://vk.com/video-1_2"]
    assert tc.has_hints is True

    dumped = tc.model_dump()
    assert dumped["hints_text"] == ["Подумай о сложении"]
    assert dumped["hints_video"] == ["https://vk.com/video-1_2"]
    assert dumped["has_hints"] is True


def test_task_content_defaults_when_hints_absent():
    """AC-2: backward compat — задача без hints получает дефолты."""
    payload = {"type": "SA", "stem": "stem"}
    tc = TaskContent.model_validate(payload)
    assert tc.hints_text == []
    assert tc.hints_video == []
    assert tc.has_hints is False

    dumped = tc.model_dump()
    assert dumped["hints_text"] == []
    assert dumped["hints_video"] == []
    assert dumped["has_hints"] is False


def test_task_content_preserves_only_video_hints():
    """AC-4 (core): только hints_video (главный кейс tsk-004 Phase 6.6 pilot)."""
    payload = {
        "type": "SA",
        "stem": "stem",
        "hints_video": ["https://vk.com/video-220754053_456239998"],
    }
    tc = TaskContent.model_validate(payload)
    dumped = tc.model_dump()
    assert dumped["hints_video"] == ["https://vk.com/video-220754053_456239998"]
    assert dumped["hints_text"] == []


# ---------- integration: bulk_upsert e2e ----------


_SOLUTION_RULES: dict[str, Any] = {
    "type": "SA",
    "accepted_answers": ["2"],
    "max_score": 1,
}


@pytest_asyncio.fixture(scope="function", autouse=True)
async def _cleanup_tsk088_course():
    """`TasksService.bulk_upsert` коммитит — нужен пост-test cleanup."""
    yield
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE course_id IN ("
                        "  SELECT id FROM courses WHERE title = 'test_tsk088'"
                        ")"
                    )
                )
                await session.execute(
                    text("DELETE FROM courses WHERE title = 'test_tsk088'")
                )
    finally:
        await engine.dispose()


async def _new_course(db: AsyncSession) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES ('test_tsk088', 'test', 'self_guided', false)
                RETURNING id
                """
            )
        )
    ).first()
    await db.flush()
    return int(row.id)


@pytest.mark.asyncio
async def test_bulk_upsert_preserves_hints_video_in_db(db: AsyncSession):
    """AC-4: bulk_upsert с hints_video → SELECT из tasks.task_content имеет hints_video."""
    course_id = await _new_course(db)
    service = TasksService()

    items = [
        {
            "external_uid": "TSK088-1",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": {
                "type": "SA",
                "stem": "stem-088-1",
                "hints_video": ["https://vk.com/video-220754053_456239998"],
                "hints_text": ["Подумай о свойствах"],
                "has_hints": True,
            },
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
    ]
    res = await service.bulk_upsert(db, items)
    assert res[0][1] == "created"
    new_id = res[0][2]

    # Читаем из БД напрямую, чтобы убедиться, что hints в jsonb сохранены
    row = (
        await db.execute(
            text(
                "SELECT task_content FROM tasks WHERE id = :tid"
            ),
            {"tid": new_id},
        )
    ).first()
    tc = row.task_content
    assert tc["hints_video"] == ["https://vk.com/video-220754053_456239998"]
    assert tc["hints_text"] == ["Подумай о свойствах"]
    assert tc["has_hints"] is True


@pytest.mark.asyncio
async def test_task_read_derives_has_hints_after_bulk_upsert(db: AsyncSession):
    """AC-3: TaskRead.has_hints derive корректно после round-trip через сервис."""
    course_id = await _new_course(db)
    service = TasksService()

    items = [
        {
            "external_uid": "TSK088-2",
            "course_id": course_id,
            "difficulty_id": 1,
            "task_content": {
                "type": "SA",
                "stem": "stem-088-2",
                "hints_video": ["https://vk.com/video-1_2"],
            },
            "solution_rules": _SOLUTION_RULES,
            "max_score": 1,
        }
    ]
    await service.bulk_upsert(db, items)

    row = (
        await db.execute(
            text(
                """
                SELECT id, task_content, course_id, difficulty_id,
                       solution_rules, external_uid, max_score, order_position
                FROM tasks
                WHERE external_uid = :uid
                """
            ),
            {"uid": "TSK088-2"},
        )
    ).first()

    read = TaskRead.model_validate(
        {
            "id": row.id,
            "task_content": row.task_content,
            "course_id": row.course_id,
            "difficulty_id": row.difficulty_id,
            "solution_rules": row.solution_rules,
            "external_uid": row.external_uid,
            "max_score": row.max_score,
            "order_position": row.order_position,
        }
    )
    assert read.hints_video == ["https://vk.com/video-1_2"]
    assert read.hints_text == []
    assert read.has_hints is True
