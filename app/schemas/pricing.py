"""Схемы тарифов курсов (tsk-505)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SaleStatus = Literal["paid", "free", "not_for_sale"]
MatchKind = Literal["attendance_frequency", "segment"]

#: Исход подбора тарифа для ученика. Разные исходы намеренно различимы:
#: «нет расписания» и «нужен выбор человека» — не одно и то же, и ни то, ни другое
#: не должно выглядеть как посчитанная цена.
PricingStatus = Literal[
    "exact",
    "fallback_lower",
    "below_grid",
    "needs_choice",
    "no_schedule",
    "no_tariff",
]


class TariffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    name: str
    price_minor: int
    currency: str
    period: str
    match_kind: Optional[MatchKind]
    match_value: Optional[str]
    is_default: bool
    sort_order: int
    is_active: bool


class TariffCreateRequest(BaseModel):
    group_id: int
    name: str = Field(min_length=1, max_length=200)
    price_minor: int = Field(ge=0)
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    period: str = Field(default="month", min_length=1, max_length=32)
    match_kind: Optional[MatchKind] = None
    match_value: Optional[str] = Field(default=None, max_length=64)
    is_default: bool = False
    sort_order: int = 0

    @model_validator(mode="after")
    def _axis_is_all_or_nothing(self) -> "TariffCreateRequest":
        if (self.match_kind is None) != (self.match_value is None):
            raise ValueError("match_kind и match_value задаются вместе или не задаются вовсе")
        if self.match_kind == "attendance_frequency":
            # Нечисловая частота не «просто не сработает»: тариф с ней выпадает
            # из подбора, и группа перестаёт считаться. Ловим на входе, потому
            # что `match_value` не правится через PATCH — опечатку пришлось бы
            # лечить удалением тарифа.
            if self.match_value is None or not self.match_value.isdigit():
                raise ValueError(
                    "Для оси «частота посещения» значение — целое число занятий в неделю"
                )
        return self


class TariffUpdateRequest(BaseModel):
    """Правка варианта тарифа, включая ось (tsk-517).

    Ось (`match_kind`/`match_value`) правится по решению оператора 2026-08-02,
    отменившему прежний запрет. Она меняет СМЫСЛ варианта: смена «2 занятия» на
    «1 занятие» переносит учеников между ценами. Поэтому экран перед сохранением
    называет число затронутых учеников, а сервер после правки пересчитывает
    открытые месяцы группы. Закрытые месяцы, как всегда, не переписываются.

    Ось задаётся парой: прислать одно поле без второго нельзя — иначе вариант
    остался бы наполовину частотным, наполовину ничьим.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    price_minor: Optional[int] = Field(default=None, ge=0)
    period: Optional[str] = Field(default=None, min_length=1, max_length=32)
    match_kind: Optional[MatchKind] = None
    match_value: Optional[str] = Field(default=None, max_length=64)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def _axis_is_all_or_nothing(self) -> "TariffUpdateRequest":
        sent = self.model_fields_set
        if ("match_kind" in sent) != ("match_value" in sent):
            raise ValueError(
                "Ось меняется целиком: match_kind и match_value присылаются вместе"
            )
        if self.match_kind is None and "match_kind" in sent:
            # Снятие оси: вариант становится единственным в группе.
            if self.match_value is not None:
                raise ValueError("Без match_kind значение оси не имеет смысла")
        if self.match_kind == "attendance_frequency":
            if self.match_value is None or not self.match_value.isdigit():
                raise ValueError(
                    "Для оси «частота посещения» значение — целое число занятий в неделю"
                )
        if self.match_kind == "segment" and not (self.match_value or "").strip():
            raise ValueError("Для оси «сегмент» нужен непустой код сегмента")
        return self


class PricingGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    is_active: bool
    tariffs: list[TariffRead] = []


class PricingGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class PricingGroupUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CoursePricingRead(BaseModel):
    """Строка экрана «Цены»: корневой курс и что с ним по деньгам."""

    course_id: int
    title: str
    course_uid: Optional[str]
    #: `None` = строки в `course_pricing` нет, курс ещё не разбирали.
    #: Это не то же самое, что `not_for_sale` — там решение принято.
    sale_status: Optional[SaleStatus]
    group_id: Optional[int]
    group_name: Optional[str]
    note: Optional[str]
    tariffs: list[TariffRead] = []
    active_students: int = Field(description="Сколько учеников сейчас зачислено на курс")


class CoursePricingUpdateRequest(BaseModel):
    sale_status: SaleStatus
    group_id: Optional[int] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _paid_requires_group(self) -> "CoursePricingUpdateRequest":
        if self.sale_status == "paid" and self.group_id is None:
            raise ValueError("Платному курсу нужна тарифная группа")
        if self.sale_status != "paid" and self.group_id is not None:
            raise ValueError("Тарифная группа имеет смысл только для платного курса")
        return self


class StudentGroupPricing(BaseModel):
    """Что насчиталось ученику по одной тарифной группе."""

    group_id: int
    group_name: str
    course_titles: list[str] = Field(
        description="Курсы этой группы, на которые зачислен ученик — цена берётся один раз на группу"
    )
    status: PricingStatus
    tariff_id: Optional[int]
    tariff_name: Optional[str]
    price_minor: Optional[int]
    #: Заполняется при `needs_choice` — из чего человеку предстоит выбрать.
    options: list[TariffRead] = []


class StudentPricingRead(BaseModel):
    student_id: int
    full_name: Optional[str]
    weekly_lessons: int = Field(description="Активных занятий в неделю по расписанию")
    groups: list[StudentGroupPricing] = []
    #: `None`, если хотя бы одна группа не посчиталась — иначе сумма выглядела бы
    #: полной, не будучи таковой.
    total_price_minor: Optional[int]
