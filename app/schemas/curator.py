"""Схемы API кураторства (tsk-742).

Три экрана: доска куратора («мои ученики, к кому идти первым»), раскладка для
методиста/админа («кто за кем закреплён и кто остался ничей») и недельный отчёт
владельцу школы («что делали кураторы»).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

AssignmentSource = Literal["derived", "manual"]
UnresolvedReason = Literal["ambiguous", "no_teacher"]


# ─── Доска куратора ──────────────────────────────────────────────────────────

class CuratorBoardStudent(BaseModel):
    """Ученик на доске куратора со всеми поводами к действию."""

    student_id: int
    student_name: Optional[str] = None
    assigned_at: datetime
    assignment_reason: Optional[str] = Field(
        default=None, description="Почему закреплён именно за этим куратором"
    )
    assignment_source: AssignmentSource

    priority: int = Field(
        ...,
        description=(
            "3 — срочно (риск ухода), 2 — просрочено, 1 — есть открытый повод, "
            "0 — ничего не требует действия. Порядок списка уже учитывает его"
        ),
    )
    reasons_to_act: List[str] = Field(
        default_factory=list, description="Что именно требует действия, словами"
    )

    open_signals: int = 0
    has_urgent: Optional[bool] = None
    oldest_signal_at: Optional[datetime] = None
    signal_reasons: Optional[str] = Field(
        default=None, description="Поводы открытых сигналов через запятую"
    )
    pending_reviews: int = 0
    oldest_submitted_at: Optional[datetime] = None
    open_help_requests: int = 0
    oldest_help_at: Optional[datetime] = None
    last_own_work: Optional[datetime] = None
    silence_days: Optional[int] = Field(
        default=None,
        description="Дней с последней СВОЕЙ работы ученика; null — не работал ни разу",
    )
    missed_lessons: int = 0
    lessons_in_window: int = 0

    last_touch_at: Optional[datetime] = Field(
        default=None, description="Когда куратор последний раз касался этого ученика"
    )
    untouched_this_week: bool = Field(
        ..., description="За неделю куратор не сделал по нему ничего"
    )


class CuratorBoardSummary(BaseModel):
    """Шапка доски: сколько всего и сколько требует действия."""

    total: int
    need_action: int
    urgent: int
    untouched_this_week: int


class CuratorThresholds(BaseModel):
    """Сроки из устава — показываются рядом, чтобы «просрочено» читалось."""

    signal_response_days: int
    urgent_response_hours: int
    review_response_days: int
    touch_window_days: Optional[int] = None


class CuratorBoardResponse(BaseModel):
    """Доска куратора целиком."""

    curator_id: int
    students: List[CuratorBoardStudent]
    summary: CuratorBoardSummary
    thresholds: CuratorThresholds


# ─── Раскладка ───────────────────────────────────────────────────────────────

class DerivedAssignment(BaseModel):
    """Предложение раскладки по одному ученику."""

    student_id: int
    student_name: Optional[str] = None
    curator_id: Optional[int] = None
    curator_name: Optional[str] = None
    reason: Optional[str] = None
    lessons_in_window: Optional[int] = None
    slot_teachers: int = 0
    lesson_teachers: int = 0
    lesson_teacher_names: Optional[str] = None
    current_curator_id: Optional[int] = None
    current_curator_name: Optional[str] = None
    current_source: Optional[AssignmentSource] = None
    unresolved_reason: Optional[UnresolvedReason] = Field(
        default=None,
        description=(
            "ambiguous — преподавателей несколько, выбирать человеку; "
            "no_teacher — по расписанию нет никого"
        ),
    )


class DerivePreviewResponse(BaseModel):
    """Что даст раскладка, если её применить. Ничего не меняет."""

    resolved: List[DerivedAssignment]
    unresolved: List[DerivedAssignment] = Field(
        ..., description="Ученики, которых закрепляет оператор руками"
    )


class ApplyDerivedRequest(BaseModel):
    """Применение раскладки."""

    dry_run: bool = Field(
        default=True,
        description="true — только посчитать; false — записать в базу",
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "false — трогать только учеников без куратора; true — переразметить "
            "и уже закреплённых, оставив след в истории"
        ),
    )


class ApplyDerivedResponse(BaseModel):
    """Итог применения раскладки."""

    dry_run: bool
    planned: List[DerivedAssignment]
    applied: int
    skipped_existing: List[DerivedAssignment]
    unresolved: List[DerivedAssignment]


class AssignCuratorRequest(BaseModel):
    """Ручное закрепление ученика за куратором."""

    curator_id: int
    reason: Optional[str] = Field(
        default=None, max_length=500,
        description="Почему именно этот куратор — видно в истории навсегда",
    )
    ended_reason: Optional[str] = Field(
        default=None, max_length=500,
        description="Почему снят прежний куратор (если он был)",
    )


class CuratorPeriod(BaseModel):
    """Один период ответственности в истории ученика."""

    id: int
    curator_id: int
    curator_name: Optional[str] = None
    assigned_at: datetime
    ended_at: Optional[datetime] = None
    source: AssignmentSource
    reason: Optional[str] = None
    ended_reason: Optional[str] = None
    assigned_by_name: Optional[str] = None
    ended_by_name: Optional[str] = None


class CuratorCoverageRow(BaseModel):
    """Сколько учеников у одного куратора."""

    curator_id: int
    curator_name: Optional[str] = None
    students: int
    manual: int = Field(..., description="Из них закреплены руками, а не правилом")


class CuratorCoverageResponse(BaseModel):
    """Сводка раскладки."""

    curators: List[CuratorCoverageRow]
    students_without_curator: int


# ─── Недельный отчёт ─────────────────────────────────────────────────────────

class CuratorWeekRow(BaseModel):
    """Строка отчёта по одному куратору за неделю."""

    curator_id: int
    curator_name: Optional[str] = None
    students: int
    students_touched: int = Field(..., description="Скольких тронул хоть как-то")
    students_acted_on: int = Field(
        ..., description="Скольких не просто посмотрел, а что-то сделал"
    )
    students_untouched: int = Field(
        ..., description="Главная цифра отчёта: скольких не тронул ни разу"
    )
    coverage: Optional[float] = Field(
        default=None, description="Доля учеников с хотя бы одним касанием"
    )
    touches_total: int
    reviews: int
    help_replies: int
    messages: int
    signals_raised: int
    signals_handled: int
    signals_overdue: int = Field(
        ..., description="Открыты дольше срока — считаются на момент отчёта"
    )
    oldest_open_signal_days: Optional[int] = None
    reviews_overdue: int


class CuratorWeeklyReportResponse(BaseModel):
    """Недельный отчёт по активности кураторов."""

    week_start: date
    week_end: date
    curators: List[CuratorWeekRow]
    students_without_curator: int
    thresholds: CuratorThresholds
    text: str = Field(..., description="Тот же отчёт словами — как в уведомлении")
