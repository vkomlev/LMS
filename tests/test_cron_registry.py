"""tsk-940: unit-покрытие общего хелпера регистрации interval-крон-джоб.

Никакого реального приложения/БД — только `AsyncIOScheduler`, созданный прямо
в тесте (по образцу `test_scheduler_first_run_survives_frequent_restarts` из
`tests/test_tsk521_link_audit.py`), и джоба-заглушка. Проверяется сам механизм
(`next_run_time`, обязательность `startup_delay_min`, проброс `hours=`/
`minutes=`, инвариантная тройка `coalesce/max_instances/replace_existing`), а
не поведение конкретного сервиса — оно проверяется регрессионными тестами
каждого мигрированного сервиса отдельно.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.cron_registry import register_interval_job


async def _noop() -> None:
    """Джоба-заглушка: тело никогда не должно выполниться в этих тестах."""


@pytest.fixture
def scheduler():
    """Планировщик без `.start()`: регистрация джобы не требует запущенного цикла."""
    sched = AsyncIOScheduler(timezone="UTC")
    yield sched
    if sched.running:
        sched.shutdown(wait=False)


@pytest.mark.asyncio
async def test_next_run_time_is_soon_after_registration(scheduler):
    """`next_run_time` — примерно `now + startup_delay_min`, а не через весь интервал."""
    before = datetime.now(timezone.utc)
    job = register_interval_job(
        scheduler,
        _noop,
        job_id="tsk940_test_next_run_time",
        startup_delay_min=5,
        hours=24,
    )
    after = datetime.now(timezone.utc)

    assert job.next_run_time is not None
    assert before + timedelta(minutes=5) <= job.next_run_time <= after + timedelta(minutes=5, seconds=5)


@pytest.mark.asyncio
async def test_missing_startup_delay_min_raises_type_error(scheduler):
    """`startup_delay_min` обязателен: забыть его — `TypeError`, а не тихий default."""
    with pytest.raises(TypeError):
        register_interval_job(  # type: ignore[call-arg]
            scheduler,
            _noop,
            job_id="tsk940_test_missing_delay",
            hours=24,
        )


@pytest.mark.asyncio
async def test_hours_kwarg_is_passed_to_interval_trigger(scheduler):
    job = register_interval_job(
        scheduler,
        _noop,
        job_id="tsk940_test_hours",
        startup_delay_min=1,
        hours=24,
    )
    assert job.trigger.interval == timedelta(hours=24)


@pytest.mark.asyncio
async def test_minutes_kwarg_is_passed_to_interval_trigger(scheduler):
    job = register_interval_job(
        scheduler,
        _noop,
        job_id="tsk940_test_minutes",
        startup_delay_min=0.5,
        minutes=2,
    )
    assert job.trigger.interval == timedelta(minutes=2)


@pytest.mark.asyncio
async def test_invariant_triple_is_always_set(scheduler):
    """`coalesce/max_instances/replace_existing` — фиксированы, не параметры."""
    job = register_interval_job(
        scheduler,
        _noop,
        job_id="tsk940_test_invariants",
        startup_delay_min=1,
        minutes=15,
    )
    assert job.coalesce is True
    assert job.max_instances == 1

    # replace_existing проверяем поведенчески: повторная регистрация с тем же
    # job_id не падает "job already exists", а заменяет джобу — так это
    # используется в start_scheduler() каждого мигрированного сервиса
    # (идемпотентный повторный вызов).
    replaced = register_interval_job(
        scheduler,
        _noop,
        job_id="tsk940_test_invariants",
        startup_delay_min=1,
        minutes=15,
    )
    assert replaced.id == job.id
    assert scheduler.get_job("tsk940_test_invariants") is not None
