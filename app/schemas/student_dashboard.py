"""
Схема периодного дашборда ученика (tsk-494). План:
docs/specs/2026-08-01-plan-tsk494-student-dashboard-api.md.

Принцип минимизации данных для менее доверенного зрителя (родителя, см.
прецедент tsk-460): полей `solution_rules`, текста заявок помощи
(`message`/`resolution_comment`), деталей `blocked_tasks` текстом в этой
схеме НЕТ ВООБЩЕ — не "добавили и скрыли постфильтром", а не добавляли.

Цветовая подсветка метрик относительно сверстников (tsk-504) — тот же
принцип: наружу отдаётся только уровень (`CohortLevel`) СВОЕГО ребёнка,
сырые значения и состав когорты других учеников в ответе не появляются
вообще (см. `app/services/student_dashboard_service.py`).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.pricing import FrequencySource
from app.schemas.retention import RetentionSummaryRead

#: Позиция ученика относительно сверстников того же курса (tsk-504) — терциль
#: распределения когорты (нижняя/средняя/верхняя треть) или явная пометка
#: недостаточности данных (когорта < порога, см. `Settings.student_dashboard_cohort_min_size`,
#: либо у самого ученика метрика не определена — напр. курс без содержимого).
CohortLevel = Literal["worse", "average", "better", "insufficient_data"]


class StudentDashboardCourseRead(BaseModel):
    course_id: int
    title: str
    percent_complete: int
    #: Темп прохождения ЭТОГО курса относительно других активных учеников
    #: этого же курса (tsk-504).
    pace_level: CohortLevel
    current_section_title: Optional[str] = None
    current_item_title: Optional[str] = None
    behind_count: int = 0
    behind_section_title: Optional[str] = None
    behind_item_title: Optional[str] = None
    forecast_completion_date: Optional[date] = None
    is_completed: bool
    is_service: bool = Field(
        default=False,
        description=(
            "tsk-921: служебный курс (tsk-877) — про устройство сервиса и "
            "экзамена, не про предмет. Клиент показывает такие одной строкой"
        ),
    )


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
    #: Доля пропусков (``missed``/``planned``) относительно других активных
    #: учеников курсов, на которые записан ребёнок (tsk-504).
    missed_level: CohortLevel


class StudentDashboardHomeworkRead(BaseModel):
    """Домашняя работа: план рядом с фактом (tsk-741).

    Все поля необязательные, и `None` здесь означает не «ноль», а «нечего
    сказать»: ученику могли ещё ничего не задавать. Ноль вместо этого читался
    бы как «не сделал» и утянул бы человека вниз в сравнении с группой.
    """

    #: Элементов в действующей выдаче; `None` — ДЗ не выдавали.
    assigned_total: Optional[int] = None
    #: Из них выполнено. Считается у источника (верная сдача / отметка материала).
    assigned_done: Optional[int] = None
    #: Срок действующей выдачи.
    due_at: Optional[datetime] = None
    #: Срок прошёл, а сделано не всё. Ничего не блокирует (решение оператора
    #: 01.09: невыполненное ДЗ — показатель, а не долг).
    is_overdue: Optional[bool] = None
    #: Доля выполненного из выданного ЗА ПЕРИОД, 0..1; `None` — выдач не было.
    completion_ratio: Optional[float] = None
    #: Та же шкала терцилей по когорте, что у посещаемости и активности
    #: (tsk-504) — своя шкала стала бы третьей на одном экране.
    level: CohortLevel


class StudentDashboardProgramRead(BaseModel):
    """Программа подготовки: успевает ли ребёнок к экзамену (tsk-815).

    Главный вопрос родителя — «успеет ли», и до сих пор ответить на него по
    дашборду было нельзя: проценты по курсам показывают, где ученик сейчас, но
    не говорят, хватит ли оставшегося времени.

    `None` вместо блока — ученик не записан ни на одну программу подготовки.
    Тогда вопрос не стоит, и пустой блок только занимал бы место.
    """

    kind: str = Field(description="ege | oge")
    deadline: date = Field(
        description="К какому дню нужно закончить программу подготовки"
    )
    remaining: int = Field(description="Сколько элементов программы осталось")
    target_per_week: int = Field(
        description=(
            "Сколько нужно в неделю, чтобы успеть. Это ОБЩИЙ темп: и дома, и на "
            "занятиях — программа не различает, где именно ребёнок её проходит"
        )
    )
    fact_per_week: float = Field(
        description="Сколько выходит сейчас — вся работа, дома и на занятиях"
    )
    lesson_share: Optional[float] = Field(
        default=None,
        description=(
            "Какая доля этой работы приходится на занятия (0..1); null — за "
            "период работы не было"
        ),
    )
    forecast_date: Optional[date] = Field(
        default=None,
        description=(
            "Когда программа будет пройдена при нынешнем темпе; null — темпа "
            "нет, предсказывать не по чему. tsk-921: та же дата стоит в "
            "`forecast_completion_date` у курсов программы — прогноз один"
        ),
    )
    on_track: bool = Field(
        description=(
            "Нынешнего темпа хватает, чтобы успеть к сроку. Считается по факту, "
            "а не по выданному объёму"
        )
    )
    early_target_per_week: Optional[int] = Field(
        default=None,
        description=(
            "Сколько нужно в неделю, чтобы закончить за этот учебный год. "
            "Только для тех, кто ещё не выпускник"
        ),
    )
    summer_target_per_week: Optional[int] = Field(
        default=None, description="То же, но с занятиями летом"
    )
    early_deadline: Optional[date] = None
    summer_deadline: Optional[date] = None
    remaining_minutes: Optional[int] = Field(
        default=None,
        description=(
            "Во сколько минут работы оценивается остаток программы (tsk-867). "
            "Оценка, а не измерение: вес задания — медиана времени «открыл → "
            "ответил» без чтения теории и без повторных попыток, а вес "
            "материала пока прокси. null — вес мерить нечем"
        ),
    )
    target_minutes_per_week: Optional[int] = Field(
        default=None,
        description="Сколько минут в неделю нужно, чтобы успеть к сроку",
    )
    fact_minutes_per_week: Optional[float] = Field(
        default=None,
        description=(
            "Сколько минут в неделю выходит сейчас. Именно эти два числа "
            "сравниваются в `on_track`: в штуках «делает 20 из нужных 20» "
            "уживалось с двукратным отставанием по времени"
        ),
    )


class StudentDashboardRead(BaseModel):
    student_id: int
    period_from: datetime
    period_to: datetime
    courses: list[StudentDashboardCourseRead]
    period_total: StudentDashboardMetricsRead
    in_class_hours: StudentDashboardMetricsRead
    between_lessons: StudentDashboardMetricsRead
    attendance: StudentDashboardAttendanceRead
    #: Активность между занятиями (``between_lessons.tasks_completed +
    #: theory_completed``) относительно других активных учеников курсов, на
    #: которые записан ребёнок (tsk-504). Не поле внутри `between_lessons` —
    #: та же форма используется и для `period_total`/`in_class_hours`,
    #: которые оператор явно исключил из подсветки.
    between_lessons_activity_level: CohortLevel
    #: tsk-741: домашняя работа — что задали, что сделано, как это выглядит на
    #: фоне группы. Рядом с активностью между занятиями намеренно: это та же
    #: работа дома, но заданная, а не свободная.
    homework: StudentDashboardHomeworkRead
    #: Серия активных недель между занятиями (tsk-032). Соседствует с
    #: `between_lessons` намеренно: это та же активность, но в виде
    #: «возвращается ли ребёнок регулярно», а не «сколько сделал за период».
    retention: RetentionSummaryRead
    #: tsk-815: успевает ли ребёнок пройти программу подготовки к сроку. Это
    #: единственный блок дашборда, который смотрит ВПЕРЁД, а не назад:
    #: остальные отвечают «что было за период», этот — «чем всё кончится».
    #: `None` — ученик не записан ни на одну программу подготовки.
    program: Optional[StudentDashboardProgramRead] = None
