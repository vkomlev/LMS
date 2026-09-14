# app/core/cron_registry.py
"""Общая регистрация interval-крон-джоб APScheduler (tsk-940).

Почему это отдельный модуль, а не копия в каждом сервисе — см.
`docs/ai/tsk940-cron-registry-scope.md`. Кратко: `add_job(IntervalTrigger(...))`
без `next_run_time` отсчитывает первый тик от момента РЕГИСТРАЦИИ джобы
(последнего рестарта процесса), а не от абсолютного времени — на проде
рестарт (деплой) случается каждые 2-4 часа, и суточные (а тем более
недельные) кроны почти не успевали тикнуть (tsk-653, tsk-939). До этого
модуля фикс был скопирован почти дословно в 6 файлах и уже разошёлся
деталями (источник `delay_min`: модуль-константа / `settings.attr` /
`getattr(settings, "...", 5)`) — копипаста продолжает расходиться, а не
стабилизируется сама.

`startup_delay_min` здесь без default осознанно: новый крон физически не
может забыть его передать — при отсутствии параметра получится `TypeError`
на старте приложения (импорт/вызов `start_scheduler()`), а не тихий баг,
который тикает раз в сутки вместо раза в несколько минут и обнаруживается
только через инцидент на проде.

`coalesce=True, max_instances=1, replace_existing=True` захардкожены внутри
функции, не параметры: на всех 14 сервисах LMS, использующих
`IntervalTrigger`, нет ни одного отклонения от этой тройки.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


def _startup_run_at(delay_min: float) -> datetime:
    """Момент первого прохода после регистрации джобы.

    Вынесено отдельной функцией, чтобы регрессионные тесты сервисов могли
    проверить `next_run_time` без ожидания реальных минут.
    """
    return datetime.now(timezone.utc) + timedelta(minutes=max(0.0, delay_min))


def register_interval_job(
    scheduler: AsyncIOScheduler,
    func: Callable[..., Any],
    *,
    job_id: str,
    startup_delay_min: float,
    **interval_kwargs: Any,
) -> Job:
    """Регистрирует interval-джобу с безопасным первым запуском.

    `startup_delay_min` обязателен без default — см. docstring модуля.
    `interval_kwargs` пробрасывается в `IntervalTrigger` как есть
    (`hours=...` / `minutes=...`), чтобы не заводить в сигнатуре отдельную
    ветку под каждую единицу интервала.
    """
    return scheduler.add_job(
        func,
        IntervalTrigger(**interval_kwargs),
        id=job_id,
        # tsk-940 (централизация фикса tsk-653/tsk-939): первый проход —
        # вскоре после старта процесса, а не через полный интервал.
        next_run_time=_startup_run_at(startup_delay_min),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
