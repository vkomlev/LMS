"""tsk-940: регрессия на `next_run_time` для code_review_cron_service.

До миграции на общий хелпер (`app/core/cron_registry.py`) у этого сервиса не
было поправки `next_run_time` вовсе — 2-минутный тик фоновой оценки кода был
живым примером БЕЗ фикса для копипасты. `IntervalTrigger(minutes=2)` без
`next_run_time` отсчитывает интервал от МОМЕНТА РЕГИСТРАЦИИ джобы, то есть от
последнего рестарта процесса — тот же класс бага, что в tsk-653 и tsk-939,
только острее: на 2-минутном интервале даже старый `startup_delay_min=5`
(скопированный вслепую с суточных сервисов) воспроизвёл бы тот же баг в
миниатюре — отсюда `code_review_cron_startup_delay_min=0.5` в
`app/core/config.py`, меньше самого интервала.

Отдельный файл, а не довесок к существующим `test_*_tsk302.py`: ни один из
них не тестирует планировщик, и там нет очевидного «домашнего» места для
этой проверки (см. `docs/ai/tsk940-cron-registry-scope.md`, Фаза 3).

`AsyncIOScheduler.start()` требует работающий event loop — отсюда
`async def`, хотя сама проверка синхронна.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import code_review_cron_service as cron


@pytest.mark.asyncio
async def test_scheduler_first_run_survives_frequent_restarts():
    cron.stop_scheduler()
    try:
        scheduler = cron.start_scheduler()
        assert scheduler is not None
        job = scheduler.get_job("tsk302_code_review_cron")
        assert job is not None
        now = datetime.now(timezone.utc)
        assert job.next_run_time <= now + timedelta(minutes=10)
    finally:
        cron.stop_scheduler()
