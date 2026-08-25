"""Схемы помощника вёрстки осеннего расписания (tsk-674, фаза 2).

Фаза 1 собрала пожелания учеников. Здесь из них собирается сетка слотов —
но собирает её МЕТОДИСТ: сервер считает спрос, предлагает набор часов и
показывает цену решения, а решение принимает человек. Автоматической
расстановки без подтверждения нет ни в одном месте этого модуля.

Сетка часов и длительность занятия берутся из `schedule_preference`: они
общие с опросом, и разъехаться им нельзя — ученик выбирал ровно те часы, из
которых методист теперь верстает.
"""
from __future__ import annotations

from datetime import time
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.schedule_preference import (
    GRID_SLOT_MINUTES,
    GRID_TIMEZONE,
    ScheduleGridDay,
)

#: Целевое наполнение слота — решение оператора: 5-6 человек.
TARGET_MIN = 5
TARGET_MAX = 6

#: Потолок: больше десяти в слот не ставим вообще.
HARD_MAX = 10

#: Наполнение слота глазами методиста.
#: `light` — меньше цели (слот дешёвый по людям, но занимает час),
#: `ok` — 5-6, ради чего всё и затевалось,
#: `crowded` — 7-10, работать можно, но нежелательно,
#: `over` — больше 10, так оставлять нельзя.
SlotLoadLevel = Literal["light", "ok", "crowded", "over"]

#: Как ученику достался час: желательный, возможный или никак.
MatchKind = Literal["preferred", "possible"]


class PlanHour(BaseModel):
    """Час сетки: день недели + начало по Москве."""

    weekday: int = Field(..., ge=0, le=6)
    start_time: time


class PlanStudentRef(BaseModel):
    """Ученик в составе слота — с пометкой, желательный ему этот час или нет."""

    student_id: int
    full_name: str | None
    timezone: str | None
    match: MatchKind
    lessons_per_week: int


class PlanSlot(BaseModel):
    """Один слот предлагаемой сетки."""

    weekday: int
    start_time: time
    students: list[PlanStudentRef]
    count: int
    level: SlotLoadLevel
    #: Уже существующий активный слот в этот час (переезжать не нужно).
    existing_slot_id: int | None
    #: Сколько человек в существующем слоте сейчас — видно цену пересборки.
    existing_student_count: int | None


class PlanDemandCell(BaseModel):
    """Спрос на один час сетки плюс то, что в этом часе стоит сегодня."""

    weekday: int
    start_time: time
    preferred_count: int
    possible_count: int
    existing_slot_id: int | None
    existing_student_count: int | None


class PlanStudentRow(BaseModel):
    """Ученик глазами вёрстки: что просил и где занимается сейчас."""

    student_id: int
    full_name: str | None
    email: str | None
    timezone: str | None
    is_filled: bool
    lessons_per_week: int
    preferred: list[PlanHour]
    possible: list[PlanHour]
    comment: str | None
    #: Слоты, в которых человек занимается сейчас (все, включая вне сетки).
    current_hours: list[PlanHour]
    #: Хотя бы один нынешний час не попадает в осеннее окно — человек переезжает.
    needs_move: bool


class PlanTeacher(BaseModel):
    """Преподаватель, на которого можно верстать."""

    teacher_id: int
    full_name: str | None
    active_slots: int


class PlanCurrentSlot(BaseModel):
    """Действующий слот расписания — то, что верстка будет менять."""

    slot_id: int
    teacher_id: int
    teacher_name: str | None
    weekday: int
    start_time: time
    duration_minutes: int
    student_count: int
    #: Попадает ли час в осеннее окно. `False` — занятие переезжает.
    in_grid: bool
    level: SlotLoadLevel
    student_ids: list[int]


class PlanCapacity(BaseModel):
    """Ёмкость школы: сколько часов есть и сколько нужно."""

    hours_total: int
    lessons_demand: int
    slots_needed_min: int
    slots_needed_max: int


class SchedulePlanSnapshot(BaseModel):
    """Всё, что нужно экрану вёрстки до первого расчёта."""

    grid: list[ScheduleGridDay]
    grid_timezone: str = GRID_TIMEZONE
    slot_minutes: int = GRID_SLOT_MINUTES
    audience_total: int
    filled_total: int
    silent_total: int
    lessons_demand: int
    demand: list[PlanDemandCell]
    students: list[PlanStudentRow]
    current_slots: list[PlanCurrentSlot]
    teachers: list[PlanTeacher]
    capacity: PlanCapacity


class PlanUnmatchedStudent(BaseModel):
    """Кому вёрстка не дала того, что он просил, — список для личного разговора."""

    student_id: int
    full_name: str | None
    timezone: str | None
    lessons_per_week: int
    #: Сколько занятий в неделю получилось поставить.
    placed: int
    #: Из них в желательные часы.
    preferred_placed: int
    preferred: list[PlanHour]
    possible: list[PlanHour]
    current_hours: list[PlanHour]
    #: Человеческая причина: «желательные часы не вошли в сетку» и т.п.
    reason: str


class PlanDayGap(BaseModel):
    """Разрыв в дне: час без занятия между двумя занятыми."""

    weekday: int
    hours: list[time]


class PlanMetrics(BaseModel):
    """Цена решения — то, по чему методист сравнивает варианты."""

    slots_total: int
    hours_total: int
    students_placed: int
    students_total: int
    lessons_planned: int
    lessons_demand: int
    #: Все занятия в желательные часы.
    fully_preferred: int
    #: Часть занятий в возможные часы (или занятий меньше, чем просил).
    partial: int
    #: Ни одного желательного часа — с этими разговаривают лично.
    without_preferred: int
    #: Никуда не встал вообще.
    unplaced: int
    slots_light: int
    slots_ok: int
    slots_crowded: int
    slots_over: int
    gap_count: int
    #: У скольких человек меняется время занятий по сравнению с сегодняшним.
    moving_students: int


class SchedulePlanPreview(BaseModel):
    """Расчёт по набору часов: что получится и чего это стоит."""

    hours: list[PlanHour]
    slots: list[PlanSlot]
    metrics: PlanMetrics
    gaps: list[PlanDayGap]
    #: Кому не досталось ни одного желательного часа (в т.ч. кто не встал вовсе).
    without_preferred: list[PlanUnmatchedStudent]
    unplaced: list[PlanUnmatchedStudent]
    grid_timezone: str = GRID_TIMEZONE
    slot_minutes: int = GRID_SLOT_MINUTES


class SchedulePlanPreviewRequest(BaseModel):
    """Тело расчёта. `hours=null` — попросить сервер подобрать набор самому."""

    hours: Optional[list[PlanHour]] = None
    teacher_id: Optional[int] = None
    #: Оставлять ли уже существующие часы в предложении (не переезжать зря).
    keep_existing: bool = True


class SchedulePlanApplySlot(BaseModel):
    """Слот, который методист утвердил."""

    weekday: int = Field(..., ge=0, le=6)
    start_time: time
    student_ids: list[int] = Field(default_factory=list)


class SchedulePlanApplyRequest(BaseModel):
    """Тело применения сетки.

    `dry_run=True` — посчитать и показать отчёт, ничего не меняя. Экран всегда
    сначала зовёт его: применение меняет расписание живым людям, и число
    затронутых человек надо увидеть ДО, а не ПОСЛЕ.
    """

    teacher_id: int
    slots: list[SchedulePlanApplySlot] = Field(default_factory=list)
    #: Погасить действующие слоты этого преподавателя, которых нет в плане.
    #: Именно так выглядит переезд утренних занятий; по умолчанию выключено.
    deactivate_missing_slots: bool = False
    dry_run: bool = True


class ApplySlotOutcome(BaseModel):
    """Что произошло с одним часом плана."""

    weekday: int
    start_time: time
    slot_id: int | None
    action: Literal["create", "reuse"]
    attached: list[int]
    detached: list[int]
    kept: list[int]


class ApplyLessonChange(BaseModel):
    """У кого меняется число занятий в неделю — там же меняется и сумма месяца."""

    student_id: int
    full_name: str | None
    before: int
    after: int


class SchedulePlanApplyResult(BaseModel):
    """Итог применения (или его предпросмотра)."""

    dry_run: bool
    slots_created: int
    slots_reused: int
    slots_deactivated: list[int]
    students_attached: int
    students_detached: int
    #: Из снимаемых — те, кто не заполнил пожелания. Раскладка их не видит, и
    #: для них это не переезд, а потеря места: отдельным полем, чтобы экран мог
    #: сказать об этом словами, а не спрятать в общем числе.
    detached_silent: list[int]
    outcomes: list[ApplySlotOutcome]
    #: Действующие слоты вне плана: их методист гасит сам, если не просил иначе.
    leftover_slots: list[PlanCurrentSlot]
    #: Смена числа занятий в неделю — сумма месяца пересчитается.
    lesson_changes: list[ApplyLessonChange]
    warnings: list[str]
