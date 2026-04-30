"""Тесты M9 миграции (Y-4.2 zombie sanitize) — INSERT zombies + upgrade +
verify санация / pending не задеты / уже-checked не задеты.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users


async def _create_student(db) -> int:
    u = Users(
        email=f"y42m9-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="m9-test", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db, *, type_: str = "MC") -> int:
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"y42m9-task-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "m9"}),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _insert_zombie(
    db, *, user_id: int, task_id: int, is_correct: bool, score: int = 0,
    received_offset_min: int = 60,
) -> int:
    """INSERT записи c is_correct IS NOT NULL AND checked_at IS NULL — zombie."""
    received = datetime.now(timezone.utc) - timedelta(minutes=received_offset_min)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct, checked_at) "
            "VALUES (:s, :u, :t, :rec, 0, :rec, 10, 'spw', :ic, NULL) RETURNING id"
        ),
        {"s": score, "u": user_id, "t": task_id, "rec": received, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _insert_pending(db, *, user_id: int, task_id: int) -> int:
    """INSERT записи с is_correct IS NULL AND checked_at IS NULL — НЕ zombie."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct, checked_at) "
            "VALUES (0, :u, :t, :now, 0, :now, 10, 'spw', NULL, NULL) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _insert_manually_checked(db, *, user_id: int, task_id: int) -> int:
    """INSERT записи с checked_at IS NOT NULL — уже проверена вручную."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct, checked_at, checked_by) "
            "VALUES (8, :u, :t, :now, 0, :now, 10, 'spw', TRUE, :now, :u) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "now": now},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, user_id: int, task_id: int, rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    await db.execute(text("DELETE FROM tasks WHERE id=:t"), {"t": task_id})
    await db.commit()


@pytest.mark.asyncio
async def test_m9_already_applied_no_new_zombies(db):
    """M9 head уже применён — global zombie count = 0."""
    cnt = (
        await db.execute(
            text(
                "SELECT count(*) FROM task_results "
                "WHERE is_correct IS NOT NULL AND checked_at IS NULL"
            )
        )
    ).scalar()
    assert cnt == 0, (
        f"После M9 миграции в БД не должно быть зомби; найдено {cnt}. "
        "Возможна регрессия: producer-side flow создаёт zombies снова."
    )


@pytest.mark.asyncio
async def test_m9_query_sanitizes_synthetic_zombies(db):
    """Применяем тот же UPDATE что в M9 — synthetic zombies санируются.

    Не запускаем alembic upgrade повторно (уже applied); вместо этого
    INSERT синтетических zombies + ручной запуск UPDATE — это валидирует
    SQL миграции на свежих данных.
    """
    user_id = await _create_student(db)
    task_id = await _create_task(db, type_="MC")
    zombies = []
    try:
        # 5 zombies
        for is_corr in (True, False, True, False, True):
            zombies.append(
                await _insert_zombie(db, user_id=user_id, task_id=task_id, is_correct=is_corr)
            )

        # Apply same UPDATE как в M9
        await db.execute(
            text(
                "UPDATE task_results "
                "SET checked_at = COALESCE(received_at, submitted_at, now()) "
                "WHERE is_correct IS NOT NULL AND checked_at IS NULL"
            )
        )
        await db.commit()

        # Verify all 5 имеют checked_at
        rows = (
            await db.execute(
                text("SELECT id, checked_at FROM task_results WHERE id = ANY(:r)"),
                {"r": zombies},
            )
        ).fetchall()
        assert len(rows) == 5
        for row in rows:
            assert row[1] is not None, f"M9 не санировал zombie id={row[0]}"
    finally:
        await _cleanup(db, user_id=user_id, task_id=task_id, rids=zombies)


@pytest.mark.asyncio
async def test_m9_does_not_touch_pending(db):
    """M9 UPDATE не должен затронуть is_correct IS NULL records."""
    user_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM")
    pendings = []
    try:
        for _ in range(3):
            pendings.append(await _insert_pending(db, user_id=user_id, task_id=task_id))
        await db.execute(
            text(
                "UPDATE task_results "
                "SET checked_at = COALESCE(received_at, submitted_at, now()) "
                "WHERE is_correct IS NOT NULL AND checked_at IS NULL"
            )
        )
        await db.commit()
        rows = (
            await db.execute(
                text("SELECT id, checked_at FROM task_results WHERE id = ANY(:r)"),
                {"r": pendings},
            )
        ).fetchall()
        assert len(rows) == 3
        for row in rows:
            assert row[1] is None, (
                f"M9 не должен затрагивать pending id={row[0]}; checked_at={row[1]}"
            )
    finally:
        await _cleanup(db, user_id=user_id, task_id=task_id, rids=pendings)


@pytest.mark.asyncio
async def test_m9_does_not_touch_already_manually_checked(db):
    """M9 UPDATE не должен переписывать checked_at у уже-проверенных вручную."""
    user_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM")
    manuals = []
    try:
        for _ in range(2):
            manuals.append(await _insert_manually_checked(db, user_id=user_id, task_id=task_id))

        # Сохраняем оригинальные checked_at
        before = {
            row[0]: row[1]
            for row in (
                await db.execute(
                    text("SELECT id, checked_at FROM task_results WHERE id = ANY(:r)"),
                    {"r": manuals},
                )
            ).fetchall()
        }

        await db.execute(
            text(
                "UPDATE task_results "
                "SET checked_at = COALESCE(received_at, submitted_at, now()) "
                "WHERE is_correct IS NOT NULL AND checked_at IS NULL"
            )
        )
        await db.commit()

        after = {
            row[0]: row[1]
            for row in (
                await db.execute(
                    text("SELECT id, checked_at FROM task_results WHERE id = ANY(:r)"),
                    {"r": manuals},
                )
            ).fetchall()
        }
        for rid in manuals:
            assert before[rid] == after[rid], (
                f"M9 не должен переписывать manually-checked id={rid}: "
                f"before={before[rid]}, after={after[rid]}"
            )
    finally:
        await _cleanup(db, user_id=user_id, task_id=task_id, rids=manuals)
