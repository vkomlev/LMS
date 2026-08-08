"""tsk-301: гонка при списании лимита наставника (пробел П7).

Модель дефекта. Наивная проверка «прочитал `used`, сравнил с лимитом, записал
`used+1`» между чтением и записью ничем не защищена. Две вкладки ученика при
остатке в одну единицу обе прочитают «осталось 1», обе пройдут проверку и обе
запишутся — итог `used = limit + 1`, то есть одна единица потрачена дважды.

Здесь проверяется, что резерв делается **одним оператором SQL**
(`INSERT … ON CONFLICT DO UPDATE … WHERE used < :limit`), и потому при остатке 1
из N одновременных попыток проходит ровно одна.

Тест работает вне общей откатываемой транзакции: гонку нельзя проверить на одном
соединении — нужны настоящие параллельные транзакции и настоящие коммиты.
Поэтому модуль убирает за собой сам.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.core.config import Settings
from app.services import entitlements_service as ent

pytestmark = [pytest.mark.asyncio, pytest.mark.no_tx_isolation]

_settings = Settings()

#: Больше одновременных попыток — шире окно гонки.
CONCURRENCY = 8


@pytest_asyncio.fixture(scope="function")
async def student_with_one_left():
    """Ученик на тарифе AI (лимит 40), у которого израсходовано 39. Уборка своя."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    period = date.today().replace(day=1)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        student_id = (
            await s.execute(
                text(
                    "INSERT INTO users (full_name, email, is_active) "
                    "VALUES ('tsk301 гонка лимита', :e, true) RETURNING id"
                ),
                {"e": f"tsk301-race-{uuid.uuid4().hex[:12]}@example.test"},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO student_subscription (student_id, plan_id, starts_on) "
                "SELECT :s, id, CURRENT_DATE FROM subscription_plan WHERE code = 'ai'"
            ),
            {"s": student_id},
        )
        await s.execute(
            text(
                "INSERT INTO student_ai_quota (student_id, period, used) "
                "VALUES (:s, :p, 39)"
            ),
            {"s": student_id, "p": period},
        )
        await s.commit()

    try:
        yield student_id
    finally:
        async with AsyncSession(engine) as s:
            for table in (
                "student_ai_grant", "student_ai_quota", "student_subscription",
            ):
                await s.execute(
                    text(f"DELETE FROM {table} WHERE student_id = :s"),
                    {"s": student_id},
                )
            await s.execute(
                text("DELETE FROM users WHERE id = :s"), {"s": student_id}
            )
            await s.commit()
        await engine.dispose()


async def _reserve_once(engine, student_id: int) -> bool:
    """Одна попытка списания в СВОЁМ соединении и СВОЕЙ транзакции."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        decision = await ent.check_and_reserve(session, student_id=student_id)
        await session.commit()
        return decision.allowed


async def test_only_one_reservation_passes_when_one_left(
    student_with_one_left: int,
) -> None:
    """При остатке 1 из восьми одновременных попыток проходит ровно одна."""
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        results = await asyncio.gather(
            *(_reserve_once(engine, student_with_one_left) for _ in range(CONCURRENCY))
        )
        assert sum(results) == 1, (
            f"списаний прошло {sum(results)} при остатке 1 — резерв не атомарен"
        )

        async with AsyncSession(engine) as s:
            used = (
                await s.execute(
                    text(
                        "SELECT used FROM student_ai_quota WHERE student_id = :s"
                    ),
                    {"s": student_with_one_left},
                )
            ).scalar()
        assert used == 40, f"счётчик ушёл за лимит: used={used}, лимит 40"
    finally:
        await engine.dispose()


async def test_grants_are_not_double_spent(student_with_one_left: int) -> None:
    """Тот же инвариант для купленного пакета: одна единица — одно списание.

    Квота исчерпана заранее, остаток только в пакете на 1 обращение.
    """
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as s:
            await s.execute(
                text(
                    "UPDATE student_ai_quota SET used = 40 WHERE student_id = :s"
                ),
                {"s": student_with_one_left},
            )
            await s.execute(
                text(
                    "INSERT INTO student_ai_grant (student_id, granted) "
                    "VALUES (:s, 1)"
                ),
                {"s": student_with_one_left},
            )
            await s.commit()

        results = await asyncio.gather(
            *(_reserve_once(engine, student_with_one_left) for _ in range(CONCURRENCY))
        )
        assert sum(results) == 1, (
            f"из пакета на 1 обращение списали {sum(results)} раз"
        )
    finally:
        await engine.dispose()
