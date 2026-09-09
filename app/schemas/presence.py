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
#:
#: tsk-835: `video` — на странице открыт видеоплеер. Отдельно от `material`
#: потому, что видео единственное, где человек может честно работать, не
#: касаясь экрана: плеер ВК живёт в кросс-доменном iframe, и нажатия внутри
#: него до страницы не доходят. Тик простоя по этому контексту тревогу
#: «молчит» не поднимает.
PresenceContext = Literal["task", "material", "course", "video", "other"]


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
        default=None,
        description=(
            "Что открыто: task | material | course | video | other. "
            "`video` — открыт видеоплеер (tsk-835): нажатия внутри плеера "
            "странице не видны, поэтому тишина здесь не означает простоя"
        ),
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

class VideoProgressRequest(BaseModel):
    """Отчёт видеоплеера о просмотре (tsk-868).

    Шлётся НАКОПЛЕННЫМ итогом, а не приращением: плеер живёт в кросс-доменном
    iframe, связь с ним рвётся на любом сообщении, и «добавь десять секунд»
    после потери пары отчётов дало бы недосчёт, который уже не восстановить.
    """

    video_id: str = Field(
        max_length=128,
        description="Идентификатор ролика у плеера: `-53400615_456240160` (ВК) или id (YouTube)",
    )
    watched_seconds: int = Field(
        ge=0, le=24 * 3600,
        description="Сколько секунд ролика реально проиграно с начала просмотра",
    )
    duration_seconds: Optional[int] = Field(
        default=None, ge=0, le=24 * 3600,
        description="Длительность ролика по данным плеера",
    )
    completed: bool = Field(
        default=False,
        description=(
            "Плеер сообщил о конце ролика. Ставится по событию, а не по доле: "
            "«досмотрел» и «доиграло, пока человек ушёл» — разные вещи"
        ),
    )
    material_id: Optional[int] = Field(default=None, ge=1)
    course_id: Optional[int] = Field(default=None, ge=1)


class VideoProgressResponse(BaseModel):
    """Ответ на отчёт — когда прислать следующий."""

    next_report_seconds: int = Field(
        description="Через сколько секунд слать следующий отчёт во время просмотра",
    )
