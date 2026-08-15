"""tsk-010 — схемы приёма оплаты.

Платёж описывает только факт денег. Сумма месяца сюда не переезжает: она
приходит из начисления (`ChargeRead`, `charge.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

PaymentMethod = Literal["manual", "gateway"]
PaymentStatus = Literal["pending", "confirmed", "rejected"]
#: За что платили (tsk-615). `monthly` — месяц обучения, всё остальное —
#: разовая покупка, у которой месяца нет.
PaymentPurpose = Literal["monthly", "ai_package"]


class PaymentRead(BaseModel):
    """Платёж глазами кабинета маркетолога.

    Месяц и тарифная группа пусты у разовой покупки: пакет обращений к
    наставнику не относится ни к какому месяцу (tsk-615).
    """

    id: int
    student_id: int
    full_name: Optional[str] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    period: Optional[date] = None
    purpose: PaymentPurpose = "monthly"
    #: Назначение словами — чтобы на экране не стоял машинный код.
    purpose_name: str = "Обучение за месяц"
    amount_minor: int
    method: PaymentMethod
    status: PaymentStatus
    #: Имя файла, которое видел загрузивший. Сам файл — отдельным запросом.
    receipt_name: Optional[str] = None
    has_receipt: bool = False
    payer_note: Optional[str] = None
    #: День, когда деньги реально ушли — по нему идёт сверка с «Мой налог».
    paid_on: Optional[date] = None
    gateway: Optional[str] = None
    gateway_payment_id: Optional[str] = None
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_name: Optional[str] = None
    created_at: datetime
    #: Сумма месяца и остаток по нему БЕЗ учёта этого платежа. Без них решение
    #: принимается вслепую: платёж на 55 000 вместо 5 500 и второй такой же чек
    #: выглядят на экране нормально, если не с чем сравнить.
    #: У разовой покупки пусто, а не ноль: ноль читался бы как «месяц оплачен».
    charge_total_minor: Optional[int] = None
    charge_due_minor: Optional[int] = None


class StudentPaymentRead(BaseModel):
    """Платёж глазами того, кто платил: без чужих имён и внутренних ссылок."""

    id: int
    amount_minor: int
    method: PaymentMethod
    status: PaymentStatus
    receipt_name: Optional[str] = None
    payer_note: Optional[str] = None
    paid_on: Optional[date] = None
    #: Почему отклонили — иначе отказ выглядит как сбой.
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class StudentPurchaseRead(BaseModel):
    """Разовая покупка глазами того, кто платил (tsk-615).

    Месяца здесь нет намеренно: покупка к нему не относится, и колонка «период»
    в таком списке только сбивала бы с толку.
    """

    id: int
    purpose: PaymentPurpose
    #: Что именно купили — словами.
    purpose_name: str
    amount_minor: int
    method: PaymentMethod
    status: PaymentStatus
    payer_note: Optional[str] = None
    paid_on: Optional[date] = None
    #: Почему отклонили — иначе отказ выглядит как пропавшие деньги.
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class StudentChargeRead(BaseModel):
    """Месяц в кабинете ученика: сколько начислено, сколько пришло, что осталось."""

    id: int
    group_id: int
    group_name: str
    period: date
    total_minor: int
    status: Literal["open", "closed"]
    #: До какого числа ждём оплату.
    due_on: date
    paid_minor: int = 0
    #: Отправлено, но ещё не подтверждено — эти деньги не считаются долгом.
    pending_minor: int = 0
    due_minor: int = 0
    #: Пришло больше, чем начислено: «долг 0» выглядит одинаково и при точной
    #: оплате, и при переплате, а разница — деньги человека.
    overpaid_minor: int = 0
    is_overdue: bool = False
    payments: list[StudentPaymentRead] = Field(default_factory=list)


class PaymentStartRequest(BaseModel):
    """Запрос на оплату картой.

    Сумму можно не указывать — тогда платим весь остаток по месяцу; это самый
    частый случай, и лишнее поле в форме только повод ошибиться.
    """

    charge_id: int
    amount_minor: Optional[int] = Field(default=None, gt=0)


class GatewayPaymentStart(BaseModel):
    """Куда отправить плательщика вводить данные карты."""

    payment_id: str
    confirmation_url: str
    amount_minor: int
    #: Платёж тестовый — деньги не спишутся. Показываем честно, а не прячем.
    test_mode: bool


class PaymentDecisionRequest(BaseModel):
    """Решение маркетолога по платежу."""

    note: Optional[str] = Field(default=None, max_length=500)


class PaymentExportRow(BaseModel):
    """Строка выгрузки для сверки с чеками «Мой налог» и с приходом в ЮKassa.

    Разовые покупки идут здесь же — у них пусты месяц и группа, а назначение
    показывает, за что взяты деньги.
    """

    id: int
    on_date: date
    full_name: Optional[str] = None
    group_name: Optional[str] = None
    period: Optional[date] = None
    purpose: PaymentPurpose = "monthly"
    purpose_name: str = "Обучение за месяц"
    amount_minor: int
    method: PaymentMethod
    gateway: Optional[str] = None
    gateway_payment_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
