"""Общий кусок тела запроса для согласия на обработку ПДн (tsk-1335).

Подмешивается в тела приёмов заявок и регистрации. Поля необязательны, пока
выключен рубильник `PD_CONSENT_REQUIRED` — старые фронты продолжают работать.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FillerRole = Literal["parent", "adult", "teen_14_17", "under_14"]


class PdConsentFields(BaseModel):
    """Галочка согласия и ответ «кто заполняет форму»."""

    consent: Optional[bool] = Field(
        default=None, description="Галочка согласия на обработку персональных данных"
    )
    filler_role: Optional[FillerRole] = Field(
        default=None,
        description="Кто заполняет: parent — родитель, adult — взрослый о себе, "
        "teen_14_17 — подросток 14–17, under_14 — младше 14 (заявка не принимается)",
    )
    page: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Страница, где дано согласие (tsk-1407); нет — берётся заголовок Referer",
    )
