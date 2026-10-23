"""tsk-997: схемы учебных достижений ученика (чтение).

Только свои числа ученика — сравнивать с другими здесь нечем, и это
закреплено схемой (как у `app/schemas/retention.py`). Баллов нет."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class StudentAchievementRead(BaseModel):
    """Полученное достижение. ``earned_at = None`` — условие уже выполнено,
    фиксация пройдёт в ближайший фоновый тик."""

    id: int
    name: str
    description: Optional[str] = None
    badge_image_url: Optional[str] = None
    earned_at: Optional[datetime] = None


class StudentAchievementNextRead(BaseModel):
    """Ближайшее невыполненное достижение своего вида и прогресс «2 из 5»."""

    id: int
    name: str
    description: Optional[str] = None
    badge_image_url: Optional[str] = None
    current: int
    target: int


class StudentAchievementsRead(BaseModel):
    """Ответ экрана достижений.

    ``enabled = False`` — показ выключен рубильником: списки пустые, клиент
    блок не рисует. Начисление в фоне при этом идёт."""

    enabled: bool
    earned: list[StudentAchievementRead] = []
    next: list[StudentAchievementNextRead] = []
