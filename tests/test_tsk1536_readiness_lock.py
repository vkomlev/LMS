"""tsk-1536: защита тика readiness от двойного запуска (advisory lock).

Вынесено в отдельный модуль намеренно, как у tsk-521/tsk-596: здесь нужны два
НЕЗАВИСИМЫХ соединения к базе, то есть собственный engine. Общая тестовая
фикстура держит одно соединение внутри savepoint — параллельный сторож
упёрся бы в обвязку теста, а не в механизм. Модуль объявлен в
`SELF_MANAGED_CONNECTION_MODULES` (tests/conftest.py) и убирает за собой сам.

Зачем механизм: на проде приложение крутится несколькими worker'ами, и тик
заведён в каждом. Тик тяжёлый (~10 с на ученика) — два одновременных тика
удвоили бы нагрузку на пул, ровно того, от чего лечил инцидент 17.11.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import settings_store
from app.core.config import Settings
from app.services import methodist_readiness_cron_service as readiness
from tests.test_tsk741_homework import (
    _program_student,
    _settings_with_program,
)

_TAG = "tsk1536lock"


async def _seed(factory) -> dict:
    """Ученик программы ЕГЭ — чтобы тику было что считать."""
    async with factory() as db:
        student_id, course_id = await _program_student(
            db, done_tasks=0, total_tasks=10, grade=11,
        )
        await db.commit()
    return {"student_id": int(student_id), "course_id": int(course_id)}


async def _cleanup(factory, env: dict) -> None:
    sid, cid = env["student_id"], env["course_id"]
    async with factory() as db:
        await db.execute(
            text("DELETE FROM methodist_readiness_cache WHERE student_id = :s"),
            {"s": sid},
        )
        await db.execute(
            text("DELETE FROM user_courses WHERE user_id = :s AND course_id = :c"),
            {"s": sid, "c": cid},
        )
        await db.execute(
            text("DELETE FROM task_results WHERE user_id = :s"), {"s": sid}
        )
        await db.execute(text("DELETE FROM tasks WHERE course_id = :c"), {"c": cid})
        await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": cid})
        await db.execute(
            text("DELETE FROM user_roles WHERE user_id = :s"), {"s": sid}
        )
        await db.execute(text("DELETE FROM users WHERE id = :s"), {"s": sid})
        await db.commit()


@pytest.mark.asyncio
async def test_second_tick_skips_while_first_holds_lock(monkeypatch):
    """Worker, зашедший под чужим локом, не считает никого.

    Внешний сторож держит advisory lock — второй тик обязан выйти сразу,
    ни один ученик не уходит в расчёт и кэш не трогается.
    """
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    env = await _seed(factory)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(env["course_id"]))
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)
    try:
        async with factory() as blocker:
            await blocker.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": readiness._LOCK_KEY},
            )
            summary = await readiness.readiness_cron_tick(session_factory=factory)
            await blocker.rollback()

        assert summary["locked"] is False
        assert summary["refreshed"] == 0
        assert summary["program_students"] == 0
        async with factory() as db:
            count = (
                await db.execute(
                    text("SELECT COUNT(*) FROM methodist_readiness_cache")
                )
            ).scalar()
        assert int(count or 0) == 0
    finally:
        await _cleanup(factory, env)
        await engine.dispose()


@pytest.mark.asyncio
async def test_tick_runs_normally_without_competition(monkeypatch):
    """Контроль того же сид-набора без конкуренции: тик работает и пишет кэш."""
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    env = await _seed(factory)
    monkeypatch.setattr(settings_store, "get_str", _settings_with_program(env["course_id"]))
    monkeypatch.setattr(settings_store, "get_bool", lambda key: True)
    try:
        summary = await readiness.readiness_cron_tick(session_factory=factory)
        assert summary["locked"] is True
        assert summary["refreshed"] == 1
        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT on_track FROM methodist_readiness_cache "
                        "WHERE student_id = :s"
                    ),
                    {"s": env["student_id"]},
                )
            ).scalar()
        assert row is not None
    finally:
        await _cleanup(factory, env)
        await engine.dispose()
