"""Схемы записи ученика в свободные слоты (tsk-674, фаза 3).

Фаза 1 собрала пожелания, фаза 2 дала методисту сверстать сетку. Здесь —
то, что происходит ПОСЛЕ вёрстки: новый ученик сам выбирает время из уже
существующих слотов.

Три правила, из которых всё остальное следует:

1. **Показываются только свободные и частично свободные слоты.** Свободный —
   меньше цели (5-6 человек), частично свободный — до потолка. Слот, где уже
   десять, не предлагается вовсе: это запрет оператора, а не подсказка.
2. **Записаться можно только в показанное.** Проверка на сервере повторяется
   в момент записи — между показом экрана и нажатием кнопки место мог занять
   другой человек.
3. **Не нашлось времени — это не тупик.** Кнопка «Не нашёл подходящее время»
   уводит заявку методисту вместе с пожеланиями ученика: либо добавляется
   слот, либо с человеком договариваются на существующий.

Пороги ЗАПИСИ (tsk-746) отдельные и строже, чем у вёрстки: методист вправе
свести в слот до десяти человек руками, но сам ученик записывается только туда,
где сейчас не больше восьми. Разница намеренная — вёрстка это решение человека
о всей школе, а запись идёт без спроса.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.schedule_plan import HARD_MAX, TARGET_MIN  # noqa: F401  (потолок вёрстки)
from app.schemas.schedule_preference import SchedulePreferenceHour

#: Потолок записи: в слот, где уже больше восьми, ученик не записывается —
#: решение оператора 31.08. Методисту потолок прежний (`HARD_MAX`), он верстает
#: осознанно и видит всю школу.
BOOKING_MAX = 8
#: Меньше этого — «мест много», зелёный сигнал.
ROOMY_BELOW = 4
#: От этого и выше — «людей уже много», предупредительный сигнал.
CROWDED_FROM = 6

#: Насколько слот свободен глазами ученика:
#: `free` — меньше четырёх, мест много;
#: `partial` — 4-5, обычное наполнение;
#: `crowded` — 6-8, людей уже много, но записаться ещё можно.
#: Слоты, где больше восьми, в ответ не попадают вовсе.
SlotAvailability = Literal["free", "partial", "crowded"]

#: Совпал ли слот с тем, что ученик просил в опросе.
SlotMatch = Literal["preferred", "possible", "none"]

#: Состояние заявки «не нашёл подходящее время».
SlotRequestStatus = Literal["open", "resolved"]


def availability_for(count: int) -> SlotAvailability:
    """Насколько слот полон глазами ученика (tsk-746).

    Три ступени вместо двух: пустой слот и слот на грани потолка человеку надо
    показывать по-разному, а «есть места» говорило и о том, и о другом.
    """
    if count < ROOMY_BELOW:
        return "free"
    if count < CROWDED_FROM:
        return "partial"
    return "crowded"


def is_bookable_count(count: int) -> bool:
    """Можно ли вообще предлагать слот с таким числом учеников.

    Порог записи — восемь, а не потолок вёрстки: расти дальше группа может
    только решением методиста.
    """
    return count <= BOOKING_MAX


class BookableSlot(BaseModel):
    """Слот, который ученику показывают как вариант записи."""

    slot_id: int
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Начало занятия по Москве")
    duration_minutes: int
    teacher_id: int
    teacher_name: str | None = None
    #: Сколько человек уже занимается в это время.
    student_count: int
    #: Сколько мест осталось до потолка. Ученику не показывается числом —
    #: экран говорит словами «есть места» / «почти набрана», — но по нему
    #: считается порядок и это же число видно методисту.
    seats_left: int
    availability: SlotAvailability
    #: Просил ли ученик этот час в опросе. Совпавшие идут первыми.
    match: SlotMatch
    #: Последний день действия слота (`None` — бессрочно).
    active_until: date | None = None
    #: Ученик уже занимается в этом слоте.
    is_mine: bool = False


class ScheduleSlotRequestRead(BaseModel):
    """Заявка «не нашёл подходящее время» — глазами и ученика, и методиста."""

    id: int
    student_id: int
    full_name: str | None = None
    email: str | None = None
    timezone: str | None = None
    comment: str | None = None
    lessons_per_week: int
    hours: list[SchedulePreferenceHour] = Field(default_factory=list)
    #: Слоты, в которых ученик уже занимается, — «пн 17:00» и т.п.
    current_slots: list[str] = Field(default_factory=list)
    status: SlotRequestStatus
    resolution_note: str | None = None
    resolved_by: int | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BookableSlotsRead(BaseModel):
    """Всё, что нужно экрану выбора времени."""

    student_id: int
    #: Входит ли человек в аудиторию расписания (выпускники и демо — нет).
    is_audience: bool
    #: Оставил ли он пожелания. Без них слоты показываются, но без пометок
    #: «желательный час», и записаться нельзя — сначала опрос.
    preference_filled: bool
    lessons_per_week: int
    #: Сколько занятий в неделю ученик уже выбрал из осенней сетки.
    booked_count: int
    #: Можно ли записаться ещё раз: ученик не набрал столько занятий, сколько
    #: сам просил. Захочет больше — сначала меняет пожелания.
    can_book_more: bool
    slots: list[BookableSlot] = Field(default_factory=list)
    #: Слоты, в которых ученик уже занимается.
    my_slots: list[BookableSlot] = Field(default_factory=list)
    grid_timezone: str
    #: Открытая заявка методисту, если ученик уже нажимал «не нашёл время».
    open_request: ScheduleSlotRequestRead | None = None


class SlotRequestWrite(BaseModel):
    """Тело кнопки «Не нашёл подходящее время»."""

    comment: str | None = Field(
        None,
        max_length=1000,
        description="Что именно не подошло — своими словами, необязательно",
    )


class SlotRequestResolve(BaseModel):
    """Методист закрывает заявку: чем всё кончилось."""

    resolution_note: str | None = Field(
        None, max_length=1000, description="Что решили с учеником"
    )


class SlotRequestList(BaseModel):
    """Очередь заявок у методиста."""

    items: list[ScheduleSlotRequestRead] = Field(default_factory=list)
    open_count: int = Field(..., description="Сколько заявок ждёт разбора")


__all__ = [
    "HARD_MAX",
    "TARGET_MIN",
    "BookableSlot",
    "BookableSlotsRead",
    "ScheduleSlotRequestRead",
    "SlotAvailability",
    "SlotMatch",
    "SlotRequestList",
    "SlotRequestResolve",
    "SlotRequestStatus",
    "SlotRequestWrite",
    "availability_for",
    "BOOKING_MAX",
    "ROOMY_BELOW",
    "CROWDED_FROM",
    "is_bookable_count",
]
