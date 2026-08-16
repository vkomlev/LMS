"""Схемы управления тарифами персоналом (tsk-301, Фаза 9).

Тариф даёт ПРАВА, расписание порождает ДЕНЬГИ — поэтому здесь нет ни одной суммы.
Присвоение тарифа только указывает, по какой группе считать месяц; сколько именно
насчитать, по-прежнему решают занятия (см. ADR-0006).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SubscriptionPlanRead(BaseModel):
    """Тариф как набор прав — строка витрины персонала."""

    code: str = Field(..., description="Машинный код: test | demo | self | ai | …")
    name: str = Field(..., description="Имя для человека")
    ai_tutor_limit: Optional[int] = Field(
        None, description="NULL — безлимит, 0 — наставника нет, N — N обращений в месяц"
    )
    code_review: bool = Field(..., description="ИИ-оценка кода")
    teacher_escalation: bool = Field(..., description="Ручной запрос помощи преподавателю")
    lessons: bool = Field(..., description="Есть ли занятия с преподавателем")
    content: str = Field(..., description="full | demo — уровень доступа к материалам")
    pricing_group_id: Optional[int] = Field(
        None, description="Группа расчёта месяца. NULL — начисления не создаются"
    )
    pricing_group_name: Optional[str] = Field(
        None, description="Имя группы: «группа 6» маркетологу ни о чём не говорит"
    )
    upgrade_hint: Optional[str] = Field(None, description="Что даёт апгрейд")
    sort_order: int = Field(..., description="Порядок показа")


class SubscriptionHistoryItem(BaseModel):
    """Одна строка истории тарифов: что действовало, когда, по чьему решению."""

    id: int
    plan_code: str
    plan_name: str
    starts_on: date
    ends_on: Optional[date] = Field(None, description="NULL — строка действующая")
    reason: Optional[str] = Field(None, description="Зачем сменили")
    changed_by: Optional[int] = Field(None, description="Кто сменил; NULL — автоматика")
    changed_by_name: Optional[str] = None
    pricing_group_id: Optional[int] = None
    pricing_group_name: Optional[str] = None


class StudentSubscriptionState(BaseModel):
    """Тариф ученика: действующий и вся история.

    История отдаётся целиком: вопрос персонала звучит как «почему у него такой
    тариф», и ответ на него — предыдущая строка с причиной и автором.
    """

    student_id: int
    current: Optional[SubscriptionHistoryItem] = Field(
        None, description="NULL — тарифа нет вовсе (права закрыты)"
    )
    history: list[SubscriptionHistoryItem] = Field(default_factory=list)


class SubscriptionChangeRequest(BaseModel):
    """Присвоение тарифа персоналом."""

    plan_code: str = Field(..., description="Код тарифа из витрины планов")
    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description=(
            "Зачем меняем. Обязательна: через месяц причина «почему у него Self» "
            "не восстанавливается ниоткуда, а тариф меняет и права, и сумму месяца"
        ),
    )

    @field_validator("plan_code", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Пробелы — не причина и не код."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("значение не может быть пустым")
        return stripped
