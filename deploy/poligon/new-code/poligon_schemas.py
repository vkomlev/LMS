"""Pydantic-схемы polygon-only эндпоинтов (tsk-182, ветка poligon — не для main).

max_length=11 в PromoApplyRequest — НАМЕРЕННЫЙ дефект класса 1 (граничные
значения), см. docs/qa-poligon/defect-registry.md. UI-подсказка на
/poligon-checkout обещает "до 12 символов" — расхождение сделано здесь,
на уровне схемы, а не случайно потеряно при рефакторинге.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CatalogCourseOut(BaseModel):
    id: int
    external_uid: str
    title: str
    price: float


class PromoApplyRequest(BaseModel):
    # Дефект класса 1: должно быть max_length=12, чтобы соответствовать
    # UI-подсказке "до 12 символов" — сознательно оставлено 11.
    code: str = Field(..., min_length=1, max_length=11)


class PromoApplyResponse(BaseModel):
    promo_applied: bool
    discount_percent: int


class CheckoutRequest(BaseModel):
    course_id: int
    promo_code: str | None = None
    payment_method: str = Field(..., pattern="^(card|wallet)$")


class CheckoutResponse(BaseModel):
    order_id: int
    promo_applied: bool
    discount_percent: int
    amount_charged: float


class EnrollRequest(BaseModel):
    course_id: int


class EnrollResponse(BaseModel):
    enrolled: bool
