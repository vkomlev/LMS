"""
Схема периодного дашборда ученика (tsk-494). План:
docs/specs/2026-08-01-plan-tsk494-student-dashboard-api.md.

Принцип минимизации данных для менее доверенного зрителя (родителя, см.
прецедент tsk-460): полей `solution_rules`, текста заявок помощи
(`message`/`resolution_comment`), деталей `blocked_tasks` текстом в этой
схеме НЕТ ВООБЩЕ — не "добавили и скрыли постфильтром", а не добавляли.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class StudentDashboardCourseRead(BaseModel):
    course_id: int
    title: str
    percent_complete: int
    current_section_title: Optional[str] = None
    current_item_title: Optional[str] = None
    forecast_completion_date: Optional[date] = None
    is_completed: bool


class StudentDashboardMetricsRead(BaseModel):
    tasks_completed: int
    theory_completed: int
    first_try: int
    help_requested_count: int


class StudentDashboardAttendanceRead(BaseModel):
    """Посещение за период по нормативу (tsk-556).

    Инвариант, на который опирается вывод: ``planned == attended + missed +
    upcoming``. Показывать `planned` без остальных трёх нельзя — цифра
    «пропущено» без нормы рядом не читается.
    """

    #: Сколько занятий период предполагал (прошедшее — по факту заведённых,
    #: хвост за горизонтом генератора — по расписанию за вычетом перерывов).
    planned: int
    #: Фактически посетил (`confirmed`/`completed`).
    attended: int
    #: Пропустил — из уже прошедших занятий периода.
    missed: int
    #: Ещё впереди: время занятия в периоде не наступило.
    upcoming: int


class StudentDashboardRead(BaseModel):
    student_id: int
    period_from: datetime
    period_to: datetime
    courses: list[StudentDashboardCourseRead]
    period_total: StudentDashboardMetricsRead
    in_class_hours: StudentDashboardMetricsRead
    between_lessons: StudentDashboardMetricsRead
    attendance: StudentDashboardAttendanceRead
