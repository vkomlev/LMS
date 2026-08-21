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


class ManualMonthAmount(BaseModel):
    """Сумма ОДНОГО месяца, поставленная руками поверх расчёта (tsk-634)."""

    period: date = Field(..., description="Первое число месяца")
    group_id: int
    group_name: str
    manual_minor: int = Field(..., description="Сумма руками, в копейках")
    calculated_minor: int = Field(..., description="Что дал бы расчёт, в копейках")


class ManualGroupPrice(BaseModel):
    """Бессрочная цена ученика В ГРУППЕ — другая сущность, чем сумма месяца."""

    group_id: int
    group_name: str
    price_minor: int = Field(..., description="Цена руками, в копейках")
    note: Optional[str] = None
    applies_now: bool = Field(
        ...,
        description=(
            "Действует ли она сейчас. False — группа покинута: цена лежит в базе "
            "и оживёт при возврате на прежний тариф, но месяц по ней не считается"
        ),
    )


class ManualPricingState(BaseModel):
    """Ручные деньги ученика — то, что смена тарифа затрагивает (tsk-634).

    Экран смены тарифа обязан это показать ДО перевода: ручная цена ставится
    там, где есть личная договорённость (скидка, особые условия), и её тихое
    изменение выглядит нормальным пересчётом, а не ошибкой.

    Две сущности ведут себя по-разному, и сводить их в одну строку нельзя:

    * `monthly_amounts` — сумма конкретного месяца. При переводе она **едет
      следом** на строку нового тарифа; со следующего месяца считается заново;
    * `group_prices` — цена ученика в группе. При переводе она **перестаёт
      действовать** (`applies_now = false`), но из базы не исчезает.
    """

    monthly_amounts: list[ManualMonthAmount] = Field(
        default_factory=list,
        description="Ручные суммы ОТКРЫТЫХ месяцев; закрытые перевод не трогает",
    )
    group_prices: list[ManualGroupPrice] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.monthly_amounts and not self.group_prices


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
    manual_pricing: ManualPricingState = Field(
        default_factory=ManualPricingState,
        description=(
            "Ручные деньги ученика: их видно ДО смены тарифа, чтобы личная "
            "договорённость не менялась молча (tsk-634)"
        ),
    )


class SubscriptionSummaryRow(BaseModel):
    """Одна строка сводки: тариф и что за ним видно (tsk-619)."""

    plan_code: Optional[str] = Field(
        ..., description="Код тарифа. NULL — строка «без тарифа»"
    )
    plan_name: str = Field(..., description="Имя тарифа или «Без тарифа»")
    pricing_group_id: Optional[int] = Field(
        ..., description="Группа расчёта месяца; NULL — начисления не создаются"
    )
    pricing_group_name: Optional[str] = Field(
        ..., description="NULL — по этому тарифу начисления не создаются"
    )
    students: int = Field(..., description="Сколько активных учеников на нём сейчас")
    with_schedule: int = Field(..., description="Из них с занятиями в расписании")
    without_schedule: int = Field(..., description="Из них без занятий")
    long_standing: int = Field(
        ...,
        description=(
            "Из них на этом тарифе дольше порога `long_standing_days`. «Второй "
            "месяц на Demo» и «зарегистрировался вчера» — разные люди"
        ),
    )
    oldest_started_on: Optional[date] = Field(
        ..., description="Самое раннее присвоение в строке; NULL — строка пуста"
    )
    with_overdue_payment: int = Field(
        ...,
        description=(
            "Из них с просроченной оплатой. Считает тот же источник, что "
            "рассылает письма о долге"
        ),
    )


class SubscriptionSummary(BaseModel):
    """Раскладка учеников по тарифам целиком.

    Пустые тарифы остаются строками с нулём, и строка «без тарифа» есть всегда:
    «на Self никого» — это ответ, а исчезнувшая строка неотличима от «мы это не
    считаем».
    """

    as_of: date = Field(..., description="День, на который посчитано")
    total_students: int = Field(
        ..., description="Всего активных учеников — сумма по строкам"
    )
    long_standing_days: int = Field(
        ..., description="Порог «засиделся», в днях (сейчас 30)"
    )
    rows: list[SubscriptionSummaryRow] = Field(
        ..., description="Строка на каждый действующий тариф плюс «без тарифа»"
    )


class SubscriptionSummaryStudent(BaseModel):
    """Ученик внутри строки сводки — чтобы из неё можно было дойти до человека."""

    student_id: int
    full_name: Optional[str] = Field(..., description="Имя ученика; NULL — не заполнено")
    plan_since: Optional[date] = Field(..., description="С какого дня на тарифе")
    days_on_plan: Optional[int] = Field(
        ..., description="Сколько дней на нём; NULL — тарифа нет"
    )
    registered_on: date = Field(
        ...,
        description=(
            "День регистрации. Рядом с `plan_since` он и отвечает на «давно ли "
            "человек в школе»: строки подписок моложе самой школы"
        ),
    )
    has_schedule: bool = Field(..., description="Есть ли занятия в расписании")
    has_overdue_payment: bool = Field(..., description="Есть ли просроченная оплата")


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
