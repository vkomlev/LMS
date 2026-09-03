"""Схемы пожеланий ученика по расписанию (tsk-674, фаза 1).

Сетка часов задана здесь константой, а не в БД: диапазон Пн-Чт 12:00-19:00 и
Сб 09:00-14:00 — решение оператора от 2026-08-25, окончательное на осень.
Держать его в таблице значило бы дать возможность тихо разъехаться клиенту и
серверу; константа приезжает на клиент вместе с ответом `GET /me/schedule-preference`,
поэтому сетка на экране и сетка в проверке — буквально одна и та же.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

#: Вид часа: желательный (самый предпочтительный) и возможный (приемлемый).
HourKind = Literal["preferred", "possible"]

#: Откуда пришла правка пожеланий.
PreferenceSource = Literal["student", "onboarding", "staff"]

#: Часы начала занятий по будням, Пн-Чт (последнее занятие 18:00-19:00 МСК).
WEEKDAY_HOURS: tuple[int, ...] = (12, 13, 14, 15, 16, 17, 18)

#: Часы начала занятий в субботу (последнее занятие 13:00-14:00 МСК).
SATURDAY_HOURS: tuple[int, ...] = (9, 10, 11, 12, 13)

#: Сетка: день недели (0=понедельник) → допустимые часы начала, МСК.
#: 33 часа в неделю — 28 по будням и 5 в субботу.
SCHEDULE_GRID: dict[int, tuple[int, ...]] = {
    0: WEEKDAY_HOURS,
    1: WEEKDAY_HOURS,
    2: WEEKDAY_HOURS,
    3: WEEKDAY_HOURS,
    5: SATURDAY_HOURS,
}

#: Пояс, в котором ведётся сетка. Совпадает с `operating_hours.timezone`.
GRID_TIMEZONE = "Europe/Moscow"

#: Умолчание, если ученик не думал про число занятий (решение оператора).
DEFAULT_LESSONS_PER_WEEK = 2

#: Длительность занятия — сетка часовая, слоты идут подряд без разрывов.
GRID_SLOT_MINUTES = 60


class SchedulePreferenceHour(BaseModel):
    """Один выбранный час: день недели, время начала по Москве, вид."""

    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Начало часа по Москве, HH:MM")
    kind: HourKind

    @field_validator("start_time")
    @classmethod
    def _whole_hour(cls, v: time) -> time:
        if v.minute or v.second or v.microsecond:
            raise ValueError("Час выбирается целиком: минуты должны быть нулевыми")
        return v


class ScheduleGridDay(BaseModel):
    """День сетки для клиента: какие часы вообще можно выбрать."""

    weekday: int
    hours: list[time]
    #: tsk-746: часы, где занятие действительно есть и куда можно встать.
    #: Остальные показываются серыми: расписание составлено, и выбор часа, в
    #: котором группы нет, оставляет человека без занятия — так 31.08 новичок
    #: выбрал четверг 17:00 и получил одно занятие вместо двух.
    open_hours: list[time] = Field(default_factory=list)
    #: tsk-786: часы, где группа ЕСТЬ, но набрана под потолок (`BOOKING_MAX`) —
    #: в `open_hours` они не попадают (выбрать нельзя), но для человека это не
    #: то же самое, что «занятий тут вообще не будет»: ученик читал пустой вид
    #: обеих закрытых ячеек одинаково и жал и в те, и в другие. Пересечения с
    #: `open_hours` нет.
    full_hours: list[time] = Field(default_factory=list)


class SchedulePreferenceRead(BaseModel):
    """Пожелания ученика + сетка, по которой они собраны."""

    student_id: int
    #: `False` — ученик пожеланий ещё не оставлял (все остальные поля — умолчания).
    is_filled: bool
    lessons_per_week: int
    hours: list[SchedulePreferenceHour]
    comment: str | None = None
    updated_at: datetime | None = None
    #: Показывать ли этому человеку опрос вообще (выпускники и демо — нет).
    is_audience: bool
    #: tsk-679: последний день нынешнего расписания ученика, если оно
    #: заканчивается (все его слоты с датой окончания). `None` — не кончается.
    schedule_ends_on: date | None = None
    #: Сетка приезжает вместе с ответом — экран рисует её, а не свою копию.
    grid: list[ScheduleGridDay]
    grid_timezone: str = GRID_TIMEZONE
    grid_slot_minutes: int = GRID_SLOT_MINUTES


class SchedulePreferenceWrite(BaseModel):
    """Тело `PUT /me/schedule-preference`.

    Проверка «желательных часов не меньше, чем занятий в неделю» живёт в
    сервисе вместе с проверкой попадания в сетку: обе про одно и то же —
    можно ли по этому пожеланию вообще собрать человеку расписание.
    """

    lessons_per_week: int = Field(
        DEFAULT_LESSONS_PER_WEEK, ge=1, le=7, description="Занятий в неделю"
    )
    hours: list[SchedulePreferenceHour] = Field(default_factory=list)
    comment: Optional[str] = Field(None, max_length=500)
    source: PreferenceSource = "student"


class SchedulePreferenceRevisionRead(BaseModel):
    """Строка истории правок."""

    id: int
    lessons_per_week: int
    hours: list[SchedulePreferenceHour]
    comment: str | None = None
    source: str
    changed_by: int | None = None
    created_at: datetime


class SchedulePreferenceStudentRow(BaseModel):
    """Строка сводки охвата: один ученик из аудитории опроса."""

    student_id: int
    full_name: str | None
    email: str | None
    timezone: str | None
    plan_code: str | None
    is_filled: bool
    lessons_per_week: int | None = None
    preferred_count: int = 0
    possible_count: int = 0
    updated_at: datetime | None = None
    #: Слоты, в которых ученик занимается сейчас, — «его нынешнее время».
    current_slots: list[str] = Field(default_factory=list)


class SchedulePreferenceDemandCell(BaseModel):
    """Спрос на один час сетки: сколько человек его просят."""

    weekday: int
    start_time: time
    preferred_count: int
    possible_count: int


class SchedulePreferenceReminderItem(BaseModel):
    """Одно напоминание из inbox — то, что заберёт student-бот TG_LMS."""

    id: int
    created_at: datetime
    kind: str
    title: str | None = None
    content: str | None = None
    payload: dict = Field(default_factory=dict)
    read_at: datetime | None = None


class SchedulePreferenceReminderPending(BaseModel):
    """Ответ `/students/{id}/schedule-preference-reminders/pending`."""

    items: list[SchedulePreferenceReminderItem]
    count: int


class SchedulePreferenceReminderRun(BaseModel):
    """Итог прохода напоминаний: кому положили, кого пропустили."""

    silent_total: int
    queued: int
    skipped_cooldown: int
    students: list[int]
    dry_run: bool = False


class SchedulePreferenceSummary(BaseModel):
    """Сводка охвата опроса для методиста и оператора."""

    #: Сколько человек СЧИТАЕТСЯ — настоящие ученики. Тестовых здесь нет.
    audience_total: int
    filled_total: int
    silent_total: int
    #: Сколько учёток опрос видят, но в счёт не идут (тестовый тариф, tsk-712).
    #: Показывается рядом со сводкой: без этого числа падение охвата читается
    #: как пропавшие ученики.
    not_counted_total: int = 0
    #: Сумма `lessons_per_week` заполнивших — спрос в посещениях в неделю.
    lessons_demand: int
    students: list[SchedulePreferenceStudentRow]
    demand: list[SchedulePreferenceDemandCell]
    grid: list[ScheduleGridDay]
    grid_timezone: str = GRID_TIMEZONE
