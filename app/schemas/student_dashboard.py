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

from app.schemas.pricing import FrequencySource


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
    #: Источник норматива из цены для ученика без расписания (tsk-557) —
    #: ``schedule``/``inferred_from_price``/``unknown``. Виден только
    #: персоналу (``can_edit_progress``: сервис/admin/methodist/teacher);
    #: `None` для родителя и гостевой ссылки — это не про ребёнка, а про то,
    #: что школа не поставила занятия (решение оператора, tsk-556).
    norm_source: Optional[FrequencySource] = None
    #: Норматив за прошедшую часть периода, выведенный из цены, минус
    #: фактически заведённые занятия. Заполняется ТОЛЬКО при
    #: ``norm_source == "inferred_from_price"`` — при активном расписании
    #: разница уже отражена в `planned` по построению, а при `unknown`
    #: считать нечем. `None` для родителя.
    not_conducted: Optional[int] = None
    #: Расписание и цена разрешились, но частоты не совпали (прод, Юлия
    #: Сесюк 4521: 1 слот в расписании, цена — по ступени «2 раза в неделю»).
    #: Норматив всё равно считается по расписанию — это только сигнал
    #: методисту сверить расписание и цену. `None` для родителя.
    discrepancy: Optional[bool] = None


class StudentDashboardRead(BaseModel):
    student_id: int
    period_from: datetime
    period_to: datetime
    courses: list[StudentDashboardCourseRead]
    period_total: StudentDashboardMetricsRead
    in_class_hours: StudentDashboardMetricsRead
    between_lessons: StudentDashboardMetricsRead
    attendance: StudentDashboardAttendanceRead
