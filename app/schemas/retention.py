"""tsk-032: схемы состояния удержания ученика (недельная серия + вехи).

Наружу отдаются только СВОИ числа ученика. Никакого сравнения с другими,
никаких значений сверстников: механики выбраны неcоревновательными осознанно
(у большинства курсов когорта меньше 5 человек, tsk-504 — соревнование втроём
чаще демотивирует), и схема этот выбор закрепляет — сравнивать тут нечем.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class RetentionAchievementRead(BaseModel):
    """Выполненная веха.

    ``earned_at`` = ``None`` означает «условие уже выполнено, но фиксация в
    `user_achievements` ещё не прошла» — веху считает то же правило, что и
    фоновый тик, поэтому ученик видит её сразу, а дата появляется в течение
    ближайшего тика (см. `retention_achievements_cron_service`)."""

    id: int
    name: str
    description: Optional[str] = None
    badge_image_url: Optional[str] = None
    earned_at: Optional[datetime] = None


class RetentionNextMilestoneRead(BaseModel):
    """Ближайшая невыполненная веха и прогресс до неё."""

    id: int
    name: str
    description: Optional[str] = None
    current: int
    target: int


class RetentionRead(BaseModel):
    """Состояние удержания ученика.

    ``weekly_streak`` — активных недель подряд, где активная неделя = хотя бы
    один день работы МЕЖДУ занятиями (время самого урока вычтено, определение
    общее с метрикой `between_lessons` дашборда родителя, tsk-494/504)."""

    weekly_streak: int
    best_weekly_streak: int
    current_week_active: bool
    current_week_days: int
    current_week_items: int
    items_between_lessons_total: int
    last_active_date: Optional[date] = None
    achievements: list[RetentionAchievementRead]
    next_milestone: Optional[RetentionNextMilestoneRead] = None


class RetentionSummaryRead(BaseModel):
    """Сводка для дашборда родителя: сигнал «возвращается ли ребёнок между
    занятиями», без витрины наград."""

    weekly_streak: int
    best_weekly_streak: int
    current_week_active: bool
    achievements_earned: int
