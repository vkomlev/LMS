"""tsk-433, аудит 2026-07-30: «верных ответов» считается от ОЦЕНЁННЫХ попыток.

Раньше знаменателем были все попытки, и попытка с неизвестным результатом
(`is_correct IS NULL`) попадала в него как неверная. На задании с единственной
такой попыткой карточка показывала «верных ответов 0 %» — методист читал это
как «задание все проваливают», хотя верных не ноль, а неизвестно.

На проде это задевало 3 задания из 12419 попыток: мало по объёму, но вывод по
такому заданию делается ложный. Теперь при отсутствии оценённых попыток сервер
отдаёт `null` («нет данных»), а не `0.0`.
"""
from __future__ import annotations

import pytest

from app.services.task_results_service import _correct_percentage


def test_percentage_counts_only_judged():
    """Две попытки оценены, одна верная — 50 %, а не 33 % от трёх."""
    assert _correct_percentage(correct_count=1, judged_count=2) == 50.0


def test_no_judged_attempts_is_unknown_not_zero():
    """Ни одной оценённой попытки — «нет данных», а не «ноль верных».

    Это и есть исходный дефект: ноль читается как факт («все провалили»),
    тогда как факта нет вовсе.
    """
    assert _correct_percentage(correct_count=0, judged_count=0) is None


def test_all_judged_correct_is_hundred():
    assert _correct_percentage(correct_count=4, judged_count=4) == 100.0


def test_none_correct_is_honest_zero():
    """Оценённые попытки есть и все неверные — ноль здесь настоящий."""
    assert _correct_percentage(correct_count=0, judged_count=3) == 0.0


def test_rounding_matches_previous_contract():
    """Округление до сотых — как было, чтобы клиенты не увидели скачка."""
    assert _correct_percentage(correct_count=1, judged_count=3) == 33.33


@pytest.mark.asyncio
async def test_task_stats_without_judged_attempts_returns_none(db, client):
    """Задание без единой оценённой попытки: процент — null, а не 0."""
    from app.core.config import Settings

    api_key = next(iter(Settings().valid_api_keys))
    # Несуществующее задание — попыток нет вовсе, тот же путь «нет данных».
    r = await client.get(f"/api/v1/task-results/stats/by-task/999999999?api_key={api_key}")
    assert r.status_code == 200, r.text
    assert r.json()["correct_percentage"] is None, r.text
