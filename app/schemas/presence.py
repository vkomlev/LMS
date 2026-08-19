"""Схемы пульса присутствия ученика (tsk-591).

``POST /api/v1/me/presence`` — короткий сигнал «я в кабинете», который шлёт SPW,
пока вкладка открыта и видима. На нём строится различение «вне системы» /
«открыл задание и молчит» для сигнала преподавателю о простое на занятии.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

#: Что открыто у ученика. Список закрытый — это подпись под текстом события в
#: ленте преподавателя («открыто задание …»), а не свободная метка клиента.
PresenceContext = Literal["task", "material", "course", "other"]


class PresenceRequest(BaseModel):
    """Тело пульса. Всё, кроме ``interacted``, необязательно."""

    interacted: bool = Field(
        default=False,
        description=(
            "Ученик что-то делал руками за прошедший интервал — печатал, "
            "касался экрана, листал страницу. False — вкладка открыта, но "
            "человек мог отойти от экрана."
        ),
    )
    context: Optional[PresenceContext] = Field(
        default=None, description="Что открыто: task | material | course | other"
    )
    course_id: Optional[int] = Field(default=None, ge=1)
    task_id: Optional[int] = Field(default=None, ge=1)
    material_id: Optional[int] = Field(default=None, ge=1)


class PresenceResponse(BaseModel):
    """Ответ на пульс — когда прислать следующий."""

    next_ping_seconds: int = Field(
        description=(
            "Через сколько секунд слать следующий пульс. Значение задаёт "
            "сервер, чтобы менять частоту без выката кабинета."
        )
    )
