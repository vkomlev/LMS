"""
Схемы Календаря LMS (tsk-428/429/430/435): часы работы школы, групповые
слоты и их участники, occurrence + явка по участнику.

Модель данных и границы MVP — docs/specs/2026-07-26-plan-kalendar-lms.md +
tsk-435 (rework на группы после встречи с реальными данными импорта).
Конвенция weekday: 0=понедельник .. 6=воскресенье (Python `date.weekday()`).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─── Operating Hours ────────────────────────────────────────────────────────


class OperatingHoursCreate(BaseModel):
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Начало часов работы школы в этот день")
    end_time: time = Field(..., description="Конец часов работы школы в этот день")
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone; MVP — одна зона на всю школу",
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> "OperatingHoursCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time должен быть позже start_time")
        return self


class OperatingHoursUpdate(BaseModel):
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    timezone: Optional[str] = None


class OperatingHoursRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weekday: int
    start_time: time
    end_time: time
    timezone: str
    created_at: datetime


# ─── Lesson Slot (групповой, tsk-435) ───────────────────────────────────────


class LessonSlotCreate(BaseModel):
    teacher_id: int = Field(..., description="ID преподавателя")
    weekday: int = Field(..., ge=0, le=6, description="0=понедельник .. 6=воскресенье")
    start_time: time = Field(..., description="Время начала занятия")
    duration_minutes: int = Field(..., gt=0, le=480, description="Длительность занятия")
    timezone: str = Field(
        default="Europe/Moscow",
        description="IANA timezone; MVP — одна зона на всю школу",
    )
    active_until: Optional[date] = Field(
        default=None,
        description="Последний день действия слота включительно; пусто — бессрочно",
    )
    student_ids: list[int] = Field(
        default_factory=list,
        description="Начальные участники слота (опционально, удобно для импорта)",
    )


class LessonSlotUpdate(BaseModel):
    """Частичная правка слота (без участников — см. отдельные эндпоинты
    `/lesson-slots/{id}/participants`)."""

    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    start_time: Optional[time] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    #: tsk-679: «действует по эту дату включительно». Установка даты убирает
    #: уже созданные занятия слота за ней — иначе календарь ученика остаётся
    #: полон занятий, которых не будет.
    active_until: Optional[date] = None
    #: Снять дату окончания (сделать слот снова бессрочным): отдельным флагом,
    #: потому что `active_until: null` в частичной правке означает «не меняем».
    clear_active_until: bool = False
    #: tsk-437: смена основного преподавателя слота. Поля не было вовсе —
    #: сменить ведущего можно было только пересозданием слота, а с ним
    #: терялись прикреплённые ученики. Будущие занятия переезжают на нового,
    #: прошедшие остаются как история.
    teacher_id: Optional[int] = None


class LessonSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    teacher_id: int
    weekday: int
    start_time: time
    duration_minutes: int
    timezone: str
    is_active: bool
    #: tsk-679: последний день действия слота включительно; `null` — бессрочно.
    #: Не то же, что `is_active=false`: слот ещё работает, но до этого дня.
    active_until: Optional[date] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    student_ids: list[int] = Field(
        default_factory=list, description="Активные участники слота (заполняется на уровне API)"
    )
    #: tsk-746: кто РЕАЛЬНО ведёт слот (`lesson_slot_teacher`). `teacher_id`
    #: выше — основной/создатель, и после перестановки преподавателей он может
    #: со слота быть снят: расписание, показывающее только его, называет не того
    #: человека. Пустой список означает «состав не задан», и тогда занятия ведёт
    #: основной — так же, как их раскатывает генератор.
    teacher_ids: list[int] = Field(
        default_factory=list, description="Кто ведёт слот (заполняется на уровне API)"
    )


class EndSlotsRequest(BaseModel):
    """Тело «расписание работает по эту дату» (tsk-679).

    `dry_run=True` — посчитать и показать отчёт, ничего не меняя. Это календарь
    живых людей: сколько занятий исчезнет и кого это коснётся, человек должен
    увидеть ДО, а не ПОСЛЕ.
    """

    last_day: date = Field(
        ..., description="Последний день действия слотов включительно"
    )
    teacher_id: Optional[int] = Field(
        default=None, description="Ограничить одним преподавателем; пусто — все"
    )
    dry_run: bool = True


class EndSlotsSlotOutcome(BaseModel):
    """Что произойдёт с одним слотом."""

    slot_id: int
    teacher_id: int
    weekday: int
    start_time: time
    #: Дата, которая у слота стояла до этого действия (обычно `null`).
    active_until: Optional[date] = None
    #: Сколько уже созданных будущих занятий уйдёт за датой.
    occurrences_removed: int
    #: Кого эти занятия касаются.
    student_ids: list[int]


class EndSlotsResult(BaseModel):
    """Итог завершения расписания (или его предпросмотра)."""

    dry_run: bool
    last_day: date
    slots_total: int
    occurrences_removed: int
    students_affected: list[int]
    slots: list[EndSlotsSlotOutcome]


class AddSlotParticipantRequest(BaseModel):
    student_id: int = Field(..., description="Ученик, добавляемый в групповой слот")


class SlotParticipantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: int
    student_id: int
    is_active: bool
    added_by: Optional[int] = None
    created_at: datetime


# ─── Lesson Occurrence + участники (tsk-429/430/435) ───────────────────────


class LessonOccurrenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: Optional[int] = None
    teacher_id: int
    scheduled_at: datetime
    duration_minutes: int
    created_at: datetime
    updated_at: datetime


class ParticipantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurrence_id: int
    student_id: int
    status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )
    rescheduled_to_occurrence_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class MyLessonOccurrenceRead(LessonOccurrenceRead):
    """Occurrence с точки зрения ОДНОГО ученика — его личный статус участия,
    без списка остальных участников группы (приватность)."""

    participant_id: int
    my_status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )


class TeacherParticipantRead(ParticipantRead):
    is_overdue: bool = Field(
        description=(
            "status='scheduled' и порог 'не пришёл' уже истёк — считается "
            "живым запросом, не ждёт следующего cron-тика"
        )
    )
    full_name: Optional[str] = Field(
        default=None,
        description=(
            "tsk-757: имя участника приходит вместе с занятием. Раньше панель "
            "подставляла его из ростера преподавателя (список по его курсам), и "
            "ученик своего же занятия, не попавший в ростер, показывался как "
            "«Ученик #id». Видимость не шире занятия: список участников уже "
            "отдаётся только владельцу занятия, методисту и админу."
        ),
    )


class TeacherLessonOccurrenceRead(LessonOccurrenceRead):
    """Occurrence в панели преподавателя — с полным списком участников."""

    participants: list[TeacherParticipantRead] = Field(default_factory=list)


class AttendanceActionRequest(BaseModel):
    action: Literal["joined", "declined"] = Field(
        ..., description="Ученик подтверждает явку или отказывается"
    )


# ─── Фаза 3 (tsk-430/435): панель преподавателя, перенос, ad-hoc ───────────


class TeacherAttendanceActionRequest(BaseModel):
    student_id: int = Field(..., description="Участник occurrence, чью явку правит преподаватель")
    action: Literal["manual_present", "manual_absent"] = Field(
        ..., description="Преподаватель вручную отмечает присутствие/отсутствие ученика"
    )


class AddStudentRequest(BaseModel):
    """Преподаватель добавляет ученика на занятие вручную (создаёт ad-hoc occurrence)."""

    teacher_id: int = Field(..., description="ID преподавателя (должен совпадать с вызывающим)")
    student_id: int = Field(..., description="ID ученика")
    scheduled_at: datetime = Field(..., description="Дата и время занятия (UTC)")
    duration_minutes: int = Field(..., gt=0, le=480)


class AddParticipantRequest(BaseModel):
    """Добавить ученика к УЖЕ существующему occurrence (например, подключить
    опоздавшего/новенького к уже идущей группе)."""

    student_id: int = Field(..., description="ID ученика")


class AdHocRequest(BaseModel):
    """Ученик сам записывается на отработку вне регулярного расписания."""

    teacher_id: int = Field(..., description="ID преподавателя")
    scheduled_at: datetime = Field(..., description="Дата и время занятия (UTC)")
    duration_minutes: int = Field(..., gt=0, le=480)


class RescheduleRequest(BaseModel):
    new_scheduled_at: datetime = Field(..., description="Новое время занятия (UTC)")


class AvailableSlotOption(BaseModel):
    scheduled_at: datetime


class BookableOccurrenceRead(BaseModel):
    """Уже существующее будущее занятие, к которому ученик может
    присоединиться (tsk-021/443) — вместо создания отдельного ad-hoc
    occurrence на то же время. Без списка остальных участников (приватность,
    тот же принцип, что `MyLessonOccurrenceRead`)."""

    id: int
    scheduled_at: datetime
    duration_minutes: int
    teacher_names: list[str] = Field(default_factory=list)


# ─── Сводки преподавателя (tsk-022 «до занятия» / tsk-410 «после занятия») ──
#
# Один общий ответ для двух точек входа фронта (решение оператора 2026-07-27):
# разворачиваемый блок в карточке occurrence ДО занятия и кнопка «Подвести
# итоги» ПОСЛЕ — одна и та же сводка, чтобы формат/источники не разошлись.


class TeacherSummaryActivity(BaseModel):
    """Последнее выполненное задание/материал ученика (не ручной зачёт)."""

    kind: Literal["task", "material"]
    title: str
    course_title: Optional[str] = None
    timestamp: datetime


class TeacherSummaryHelpRequest(BaseModel):
    """Заявка на помощь (открытая или закрытая в текущем окне ДЗ) — с текстом,
    не только счётчик."""

    request_id: int
    task_id: Optional[int] = Field(
        default=None, description="Для ссылки на задание (тот же аффорданс, что у blocked_tasks)"
    )
    task_title: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime
    resolution_comment: Optional[str] = Field(
        default=None,
        description="Комментарий преподавателя при закрытии заявки — только у закрытых",
    )


class TeacherSummaryBlockedTask(BaseModel):
    """Задание, заблокированное лимитом попыток (текущий снепшот)."""

    task_id: int
    title: str
    course_title: Optional[str] = None


class TeacherSummaryCourseProgress(BaseModel):
    course_id: int
    title: str
    percent_complete: int = Field(..., ge=0, le=100)
    current_section_title: Optional[str] = Field(
        default=None,
        description="Раздел/подкурс, где сейчас ученик — родительский узел ближайшего "
        "незавершённого элемента дерева курса; None, если незавершённый элемент лежит "
        "прямо в корне курса (раздел совпадает с самим курсом) или курс пройден целиком",
    )
    current_item_title: Optional[str] = Field(
        default=None,
        description="Конкретное следующее незавершённое задание/материал в этом курсе; "
        "None, если курс пройден целиком (не путать с last_activity — тот про "
        "последнее ЗАВЕРШЁННОЕ действие, этот про следующее НЕзавершённое)",
    )


class TeacherSummaryHomework(BaseModel):
    """Метрики ДЗ за окно «между занятиями» (с конца предыдущего occurrence
    этого ученика до момента запроса). tsk-473: раздельные счётчики —
    операторский откат объединения от 2026-07-27 (tsk-410)."""

    tasks_completed: int = Field(..., description="Заданий сдано верно в окне")
    theory_completed: int = Field(..., description="Материалов (теории) изучено в окне")
    first_try: int = Field(
        ..., description="Из заданий (не материалов) — сколько верно с первой попытки"
    )
    help_requested: int = Field(..., description="Заявок на помощь создано в окне")


class TeacherSummaryParticipant(BaseModel):
    student_id: int
    full_name: Optional[str] = None
    tg_id: Optional[int] = None
    timezone: Optional[str] = Field(
        default=None,
        description=(
            "tsk-588: часовой пояс ученика (IANA id) или None, если не заполнен. "
            "Расписание школы ведётся по Москве — разница видна рядом с именем."
        ),
    )
    status: str = Field(
        description="scheduled | confirmed | declined | rescheduled | no_show | completed"
    )
    is_overdue: bool
    last_activity: Optional[TeacherSummaryActivity] = None
    days_since_last_activity: Optional[int] = None
    window_from: Optional[datetime] = Field(
        default=None,
        description="Начало окна ДЗ для ЭТОГО ученика — конец его предыдущего occurrence "
        "(у каждого участника группы своя история занятий); None — предыдущего не было",
    )
    homework: TeacherSummaryHomework
    blocked_tasks: Optional[list[TeacherSummaryBlockedTask]] = Field(
        default_factory=list,
        description=(
            "tsk-665: `null` — не считалось (запрос без `include_progress`), "
            "пустой список — посчитали, заблокированных нет. Спутать эти два "
            "смысла нельзя: первый требует запроса подробностей, второй нет."
        ),
    )
    open_help_requests: list[TeacherSummaryHelpRequest] = Field(default_factory=list)
    closed_help_requests: list[TeacherSummaryHelpRequest] = Field(
        default_factory=list,
        description="Заявки, закрытые в том же окне ДЗ, что и остальные метрики "
        "(не вся история — иначе список рос бы неограниченно)",
    )
    missed_streak: int = Field(
        description="Пропущенных ПОДРЯД последних занятий (0 — пришёл на последнее/это первое)"
    )
    course_progress: Optional[list[TeacherSummaryCourseProgress]] = Field(
        default_factory=list,
        description="tsk-665: `null` — не считалось; пустой список — курсов нет.",
    )


class TeacherLessonOccurrenceSummaryRead(BaseModel):
    occurrence_id: int
    is_ad_hoc: bool = Field(description="Occurrence вне регулярного расписания (slot_id IS NULL)")
    window_to: datetime = Field(description="Конец окна ДЗ — момент запроса сводки, общий для всех")
    participants: list[TeacherSummaryParticipant] = Field(default_factory=list)
