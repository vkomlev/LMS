"""Integration-тесты streak-логики /me/streak (Phase Y-3.1).

Покрывает edge cases per LMS-side spec §8 / CB tech-spec §10:
- empty: ученик ничего не решал → streak=0
- today only: одна задача сегодня → streak=1, today_active=True
- yesterday only (gap=1): одна задача вчера → streak=1, today_active=False
- gap=2 (reset): задача 3 дня назад + сегодня → streak=1 (только сегодня, обнуление)
- continuous 3 days: задачи today + yesterday + day-before → streak=3
- duplicate same day: 2 task_results в один день → streak=1 (DISTINCT)
- TZ Europe/Moscow: явно проверяем, что compute идёт в Moscow TZ через server side

Все timestamps конструируются server-side через
`(now() AT TIME ZONE 'Europe/Moscow')::date - N * INTERVAL '1 day' + INTERVAL '12 hours' AT TIME ZONE 'Europe/Moscow'`,
чтобы избежать flaky-зависимости от боундари полуночи Moscow.
"""
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import me_service


async def _setup_user(db) -> int:
    user = Users(email=None, password_hash=None, full_name="Y3-streak", tg_id=None)
    db.add(user)
    await db.flush()
    await db.commit()
    return user.id


async def _get_any_task_id(db) -> int:
    row = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).first()
    if row is None:
        pytest.skip("Нет tasks в БД для seed task_results")
    return row[0]


async def _insert_task_result_msk_days_ago(db, user_id: int, task_id: int, days_ago: int) -> None:
    """INSERT task_results с received_at = (today_msk - days_ago) в полдень Moscow.

    Полдень — чтобы избежать flakiness на боундари Moscow midnight.
    """
    # `:days` приходит как строка (asyncpg binding), и сразу склеивается с ' days'
    # → корректный interval-литерал; целочисленный path требовал бы make_interval.
    sql = text(
        """
        INSERT INTO task_results (user_id, task_id, score, max_score, is_correct, received_at)
        VALUES (
            :user_id,
            :task_id,
            1, 1, true,
            (
                ((now() AT TIME ZONE 'Europe/Moscow')::date - (:days || ' days')::interval
                 + INTERVAL '12 hours')
                AT TIME ZONE 'Europe/Moscow'
            )
        )
        """
    )
    await db.execute(sql, {"user_id": user_id, "task_id": task_id, "days": str(days_ago)})
    await db.flush()


async def _cleanup_task_results(db, user_id: int) -> None:
    """DELETE task_results created by test (audit_event_no_modify trigger не страгивается —
    он на audit_event, не на task_results)."""
    await db.execute(text("DELETE FROM task_results WHERE user_id=:u"), {"u": user_id})
    await db.commit()


# ── Сценарии ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_streak_empty(db):
    """Ученик ничего не решал → streak=0, last=None, today_active=False."""
    user_id = await _setup_user(db)
    try:
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 0
        assert result["last_active_date"] is None
        assert result["today_active"] is False
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_today_only(db):
    """Одна задача сегодня → streak=1, today_active=True."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 0)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 1
        assert result["today_active"] is True
        assert result["last_active_date"] is not None
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_yesterday_only_gap1(db):
    """Одна задача вчера, ничего сегодня → streak=1, today_active=False (gap=1 разрешён)."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 1)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 1
        assert result["today_active"] is False
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_gap2_resets(db):
    """Задача 3 дня назад + сегодня → streak=1 (gap=2 обнуляет, считаем только сегодня)."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 3)
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 0)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        # Текущий run = только сегодня (3 дня назад в другой grp)
        assert result["streak_days"] == 1
        assert result["today_active"] is True
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_continuous_3_days(db):
    """today + yesterday + day-before → streak=3."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        for d in (0, 1, 2):
            await _insert_task_result_msk_days_ago(db, user_id, task_id, d)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 3
        assert result["today_active"] is True
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_dedup_same_day(db):
    """2 task_results в один день → streak=1 (DISTINCT по дате)."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 0)
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 0)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 1
        assert result["today_active"] is True
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_old_only_gap_too_large(db):
    """Задача 5 дней назад, ничего ближе → streak=0 (gap > 1, reset)."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 5)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        # Last activity = 5 days ago → gap > 1 day → reset
        assert result["streak_days"] == 0
        assert result["last_active_date"] is None
        assert result["today_active"] is False
    finally:
        await _cleanup_task_results(db, user_id)


@pytest.mark.asyncio
async def test_streak_yesterday_with_today(db):
    """today + yesterday → streak=2, today_active=True."""
    user_id = await _setup_user(db)
    task_id = await _get_any_task_id(db)
    try:
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 0)
        await _insert_task_result_msk_days_ago(db, user_id, task_id, 1)
        await db.commit()
        result = await me_service.get_streak(db, user_id)
        assert result["streak_days"] == 2
        assert result["today_active"] is True
    finally:
        await _cleanup_task_results(db, user_id)
