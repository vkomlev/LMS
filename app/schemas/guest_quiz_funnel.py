"""Схема воронки квизов для кабинета маркетолога (tsk-053, фаза 1).

Отдельным модулем от `guest_quiz`: тот описывает публичный контур посетителя, этот —
внутреннюю отчётность под ролевым гейтом. Смешивать их в одном файле значило бы
держать рядом то, что видит аноним, и то, что видит только маркетолог.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class QuizFunnelRow(BaseModel):
    """Строка воронки по одному квизу."""

    course_uid: Optional[str] = None
    title: str
    total_questions: int = Field(..., description="Сколько активных вопросов в квизе")
    started: int = Field(..., description="Гостевых сессий, ответивших хотя бы на один вопрос")
    completed: int = Field(..., description="Из них прошли квиз до конца")
    leads: int = Field(..., description="Заявок с контактом, оставленных по этому квизу")
    lead_rate: Optional[float] = Field(
        None,
        description=(
            "Доля дошедших до заявки среди прошедших квиз до конца. null — квиз "
            "ещё никто не завершил, делить не на что"
        ),
    )
