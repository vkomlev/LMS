"""Сколько задавать на дом: норма из темпа ученика и срока до экзамена (tsk-741).

Формула согласована с оператором 01.09.2026 и построена на замере боевой базы:
у 60 учеников курсов ЕГЭ медиана — 35 верных сдач за 4 недели (≈9 в неделю),
p90 — 122 (≈30), 18 человек не решили за месяц ничего. При этом одиннадцати-
класснику, чтобы пройти 797 заданий до июня 2027, нужно ≈20 в неделю. Разрыв
между «надо» и «делает» — это и есть то, что система обязана показывать.

    цель   = недельная норма класса (11 → 20, 10 и 9 → 12, младше → 8)
    факт   = медиана завершённых элементов за 3 полные недели
    объём  = clamp( min(цель, факт × 1.2), 3, 25 ), но не больше остатка программы

**Почему цель задаётся классом напрямую, а не выводится из остатка программы.**
Первая редакция считала `надо = остаток / (недель до экзамена × 0.85)` — и это
не выдержало проверки на живых данных 01.09. Курс «ЕГЭ по информатике» — это
**банк из 1758 заданий**, а не конечная программа: остаток у учеников 1700-4800
элементов, `надо` выходило 52-58 в неделю у ВСЕХ и всегда упиралось в потолок.
Следствие было хуже арифметики: `min(надо, факт × 1.2)` всегда выбирал вторую
часть, и класс переставал влиять на объём вовсе — то есть весь смысл вопроса
про класс (фаза 1 этой же задачи) пропадал.

Поэтому срок до экзамена остался тем, что он есть — **пояснением**
(`weeks_to_exam`, видно преподавателю), а нагрузку задаёт целевая норма класса.
Цифра 20 для 11 класса взята из того же замера: столько нужно, чтобы пройти
базовый курс «Python для ЕГЭ» (797 заданий) за 39 недель до июня 2027. У 10
класса год в запасе, у 9 программа легче — им 12.

Что важно помнить читающему:

- **Единица нормы — элемент программы, а не задание.** Материалы (теория) —
  такая же домашняя работа: прямое требование оператора «теорию учат дома,
  чтобы занятие сместилось к заданиям». Потолок 25 откалиброван по p90 сдач
  заданий, то есть заведомо не занижен.
- **Медиана, а не среднее.** Один запойный вечер на 200 задач не должен
  задирать норму на месяц вперёд; на проде такие всплески есть (максимум за
  неделю — 1400 строк).
- **Считается только то, что ученик сделал САМ.** Ручные зачёты преподавателя
  ставятся пачками (у одного ученика 660 ручных против 4 настоящих сдач,
  tsk-656) — правило берётся из `learning_gaps_service`, а не пишется заново.
- **Не грузим больше, чем человек тянет** (`факт × 1.2`): норма растёт
  ступеньками. Если человек не дотягивает до нормы своего класса — это не
  повод завалить его заданиями, а сигнал преподавателю: он виден в `pace_gap`.
- **Качество важнее скорости.** Доля верных ниже 60% — объём уменьшается на
  четверть: человек тонет, добавлять ему задания вредно (решение оператора
  01.09: «скорость с поправкой на качество»). Поправка не применяется, пока
  сдач слишком мало для вывода.
- **Класс неизвестен → считаем как 11** (решение оператора 01.09): ошибиться
  в сторону более короткого срока безопаснее. Сам класс собирается вопросом в
  кабинете, фаза 1 той же задачи.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    real_student_material_filter,
    real_student_results_filter,
)
# tsk-741: «что вообще входит в программу» — одно правило на весь проект.
from app.services.manual_progress_service import REQUIREMENT_LEVELS

logger = logging.getLogger(__name__)

#: Меньше этого на дом не задаём — иначе выдача теряет смысл.
MIN_PER_WEEK = 3
#: Больше этого не задаём никогда: выше p90 нынешнего темпа ДЗ просто
#: перестают делать, и невыполнимая норма обесценивает саму механику.
MAX_PER_WEEK = 25
#: На сколько норма может превышать сегодняшний темп ученика за один шаг.
GROWTH_FACTOR = 1.2
#: Целевая недельная норма по классу, элементов программы.
#: 11 класс — выпускной, полный ход: столько нужно, чтобы пройти базовый курс
#: (797 заданий) за 39 недель до июня. 10 класс — год в запасе, 9 класс — ОГЭ
#: этим летом, но программа легче. Младше 9 — щадящий режим: экзамен далеко.
TARGET_PER_WEEK_BY_GRADE: dict[int, int] = {11: 20, 10: 12, 9: 12}
#: Норма для тех, кто младше девятого класса.
TARGET_PER_WEEK_JUNIOR = 8

#: С этого месяца выпускной класс уходит на отработку вариантов (1-2 варианта в
#: неделю целиком), и времени на обычное ДЗ почти не остаётся. Норма падает —
#: иначе система весь финиш будет показывать «не дотягивает», хотя человек как
#: раз занят главным. Решение оператора 01.09.2026.
EXAM_SPRINT_FROM_MONTH = 3
#: Недельная норма выпускного класса на финише: остаток времени держим за
#: вариантами, домашняя работа становится добавкой, а не основой.
TARGET_PER_WEEK_EXAM_SPRINT = 6

#: Остатка программы меньше, чем на столько недель — пора добавлять курс.
#: Сигнал поднимается заранее: «программа кончилась» узнавать в тот день, когда
#: ученику нечего задать, поздно (вопрос оператора 01.09: с опережением графика
#: без ДЗ не оставляем).
PROGRAM_LOW_WEEKS = 4
#: Сколько полных недель берём для оценки фактического темпа.
FACT_WEEKS = 3
#: Доля верных, ниже которой человек считается тонущим.
QUALITY_THRESHOLD = 0.6
#: Во сколько раз уменьшаем объём тонущему.
QUALITY_PENALTY = 0.75
#: Меньше этого числа сдач — о качестве судить не по чему.
MIN_QUALITY_SAMPLE = 5
#: Класс, по которому считаем тех, чей класс неизвестен (решение оператора).
ASSUMED_GRADE = 11
#: Месяц и день основного периода экзаменов — начало июня.
EXAM_MONTH = 6
EXAM_DAY = 1


@dataclass(frozen=True)
class VolumePlan:
    """Норма домашней работы и всё, из чего она сложилась.

    Состав полей — не отладочный: ровно это уходит в `volume_details` выдачи и
    показывается преподавателю. Число без объяснения («задать 12») никто не
    сможет ни оспорить, ни проверить.
    """

    #: Класс ученика; None — не указан (тогда считали по ASSUMED_GRADE).
    grade: Optional[int]
    #: True — класс не известен, срок взят пессимистично.
    grade_assumed: bool
    #: Дата ближайшего для этого класса экзамена.
    exam_date: date
    #: Недель до экзамена (может быть дробным).
    weeks_to_exam: float
    #: Незавершённых элементов программы. Не знаменатель нормы (курс — банк
    #: заданий, а не конечная программа), а потолок: больше, чем осталось, не
    #: задашь.
    remaining_items: int
    #: Целевая недельная норма ЭТОГО класса.
    target_per_week: int
    #: Сколько человек делает сейчас (медиана за FACT_WEEKS недель).
    fact_per_week: float
    #: Доля верных сдач за то же окно; None — сдач слишком мало.
    correct_ratio: Optional[float]
    #: True — объём уменьшен из-за низкой доли верных.
    quality_penalty_applied: bool
    #: Итоговая норма на неделю.
    volume_per_week: int
    #: На сколько недель хватит остатка программы при этой норме; None — норма
    #: нулевая (задавать нечего). Это ответ на «что делать с теми, кто идёт с
    #: опережением»: их видно ЗАРАНЕЕ, а не в день, когда задавать стало нечего.
    weeks_of_program_left: Optional[int]
    #: Программы осталось меньше чем на PROGRAM_LOW_WEEKS недель (или её нет
    #: вовсе) — пора добавлять ученику курс.
    needs_more_program: bool
    #: True — норма снижена, потому что выпускной класс с марта отрабатывает
    #: варианты, а не проходит новое.
    exam_sprint: bool
    #: На сколько элементов в неделю человек не дотягивает до нормы своего
    #: класса; 0 — дотягивает. Это и есть сигнал преподавателю: не «завалить
    #: заданиями», а «видно, что отстаёт».
    pace_gap: int

    def as_details(self) -> dict[str, Any]:
        """Снимок для `homework_assignment.volume_details` (JSON-совместимый)."""
        data = asdict(self)
        data["exam_date"] = self.exam_date.isoformat()
        return data


def exam_date_for(grade: Optional[int], today: date) -> date:
    """Дата ближайшего экзамена для класса.

    Экзаменные классы — 9 (ОГЭ) и 11 (ЕГЭ); всем, кто младше, считаем срок до
    ближайшего из них. Учебный год отсчитывается от июня: в сентябре 2026
    одиннадцатиклассник сдаёт в июне 2027, десятиклассник — в июне 2028.

    Args:
        grade: класс 1-11 или None (тогда ASSUMED_GRADE).
        today: сегодняшняя дата.

    Returns:
        Дата начала основного периода экзаменов.
    """
    effective = grade if grade is not None else ASSUMED_GRADE
    target = 9 if effective <= 9 else 11
    years_left = max(target - effective, 0)

    this_year_exam = date(today.year, EXAM_MONTH, EXAM_DAY)
    base_year = today.year if today <= this_year_exam else today.year + 1
    return date(base_year + years_left, EXAM_MONTH, EXAM_DAY)


def target_per_week_for(grade: Optional[int], today: Optional[date] = None) -> int:
    """Целевая недельная норма для класса на эту дату.

    Класс не указан — считаем как 11 (решение оператора 01.09): ошибиться в
    сторону более высокой нагрузки безопаснее, чем оставить выпускника без неё.
    Норма — не приговор: выше того, что человек тянет, объём всё равно не
    поднимется (`факт × 1.2`).

    **С марта у выпускного класса норма падает.** С этого месяца одиннадцатый
    класс переходит на отработку вариантов — 1-2 полных варианта в неделю, — и
    времени на обычное ДЗ почти не остаётся. Оставить прежние 20 значило бы
    весь финиш показывать преподавателю «не дотягивает», хотя ученик занят
    ровно тем, чем должен. Считается по месяцу ЭКЗАМЕНАЦИОННОГО года: в марте
    2027 выпускник 2027 года уже на финише, а десятикласснику до его марта
    ещё год.

    Args:
        grade: класс 1-11 или None.
        today: дата расчёта; None — сегодня.

    Returns:
        Сколько элементов в неделю считать нормой.
    """
    effective = grade if grade is not None else ASSUMED_GRADE
    moment = today or date.today()

    if effective >= 11 or grade is None:
        exam_day = exam_date_for(grade, moment)
        sprint_start = date(exam_day.year, EXAM_SPRINT_FROM_MONTH, 1)
        if sprint_start <= moment <= exam_day:
            return TARGET_PER_WEEK_EXAM_SPRINT

    if effective in TARGET_PER_WEEK_BY_GRADE:
        return TARGET_PER_WEEK_BY_GRADE[effective]
    return TARGET_PER_WEEK_JUNIOR


_REMAINING_SQL = f"""
WITH RECURSIVE tree AS (
    SELECT uc.course_id AS member_course_id
      FROM user_courses uc
     WHERE uc.user_id = :student_id AND uc.is_active = true
    UNION
    SELECT cp.course_id
      FROM tree t
      JOIN course_parents cp ON cp.parent_course_id = t.member_course_id
),
course_tasks AS (
    SELECT DISTINCT t.id
      FROM tasks t JOIN tree ON tree.member_course_id = t.course_id
     WHERE COALESCE(t.is_active, true)
       AND t.requirement_level = ANY(:levels)
),
course_materials AS (
    SELECT DISTINCT m.id
      FROM materials m JOIN tree ON tree.member_course_id = m.course_id
     WHERE COALESCE(m.is_active, true)
       AND m.requirement_level = ANY(:levels)
),
tasks_done AS (
    SELECT DISTINCT tr.task_id AS id
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
       AND tr.task_id IN (SELECT id FROM course_tasks)
    UNION
    SELECT stp.task_id
      FROM student_task_progress stp
     WHERE stp.student_id = :student_id AND stp.status = 'skipped'
       AND stp.task_id IN (SELECT id FROM course_tasks)
),
materials_done AS (
    SELECT DISTINCT smp.material_id AS id
      FROM student_material_progress smp
     WHERE smp.student_id = :student_id AND smp.status IN ('completed', 'skipped')
       AND smp.material_id IN (SELECT id FROM course_materials)
)
SELECT (SELECT count(*) FROM course_tasks) + (SELECT count(*) FROM course_materials)
         AS total_items,
       (SELECT count(*) FROM tasks_done) + (SELECT count(*) FROM materials_done)
         AS done_items
"""

#: Завершённое ЗА НЕДЕЛЮ, по неделям — для медианы фактического темпа.
#: Ручные зачёты отсечены общим правилом проекта, а не своей копией условия.
_FACT_SQL = f"""
WITH weeks AS (
    SELECT generate_series(0, :weeks - 1) AS idx
),
task_done AS (
    SELECT date_trunc('week', tr.submitted_at AT TIME ZONE 'Europe/Moscow') AS wk,
           count(DISTINCT tr.task_id) AS n
      FROM task_results tr
      JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
     WHERE tr.user_id = :student_id AND tr.is_correct = true
       AND {real_student_results_filter('tr')}
       AND tr.submitted_at >= :since
     GROUP BY 1
),
material_done AS (
    SELECT date_trunc('week', smp.completed_at AT TIME ZONE 'Europe/Moscow') AS wk,
           count(DISTINCT smp.material_id) AS n
      FROM student_material_progress smp
     WHERE smp.student_id = :student_id AND smp.status = 'completed'
       AND smp.completed_at IS NOT NULL
       AND {real_student_material_filter('smp')}
       AND smp.completed_at >= :since
     GROUP BY 1
)
SELECT w.wk::date AS week,
       COALESCE(td.n, 0) + COALESCE(md.n, 0) AS done
  FROM (
        -- CAST(...), а не `:since::timestamptz`: SQLAlchemy НЕ считает
        -- параметром имя, за которым идёт двоеточие, и `:since` уехал бы в
        -- запрос буквально — синтаксическая ошибка в неочевидном месте.
        SELECT date_trunc(
                   'week',
                   (CAST(:since AS timestamptz) AT TIME ZONE 'Europe/Moscow')
                   + CAST(idx || ' weeks' AS interval)
               ) AS wk
          FROM weeks
       ) w
  LEFT JOIN task_done td ON td.wk = w.wk
  LEFT JOIN material_done md ON md.wk = w.wk
 ORDER BY 1
"""

#: Доля верных за то же окно — поправка на качество.
_QUALITY_SQL = f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE tr.is_correct) AS correct
  FROM task_results tr
  JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
 WHERE tr.user_id = :student_id
   AND {real_student_results_filter('tr')}
   AND tr.submitted_at >= :since
"""


async def compute(
    db: AsyncSession, *, student_id: int, now: Optional[datetime] = None
) -> VolumePlan:
    """Посчитать норму домашней работы для ученика.

    Только чтение: ничего не пишет и не выдаёт — выдачей занимается
    `homework_service`. Отдельная функция затем, чтобы норму можно было
    показать преподавателю и проверить, не задавая ничего.

    Args:
        db: async session.
        student_id: ID ученика.
        now: момент расчёта (для тестов); по умолчанию — сейчас.

    Returns:
        `VolumePlan` — норма и всё, из чего она сложилась.
    """
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(weeks=FACT_WEEKS)

    grade = (
        await db.execute(
            text("SELECT school_grade FROM users WHERE id = :uid"), {"uid": student_id}
        )
    ).scalar()

    totals = (
        await db.execute(
            text(_REMAINING_SQL),
            {"student_id": student_id, "levels": list(REQUIREMENT_LEVELS)},
        )
    ).mappings().one()
    remaining = max(int(totals["total_items"]) - int(totals["done_items"]), 0)

    weekly = [
        int(r["done"])
        for r in (
            await db.execute(
                text(_FACT_SQL),
                {"student_id": student_id, "since": since, "weeks": FACT_WEEKS},
            )
        ).mappings()
    ]
    fact_per_week = float(statistics.median(weekly)) if weekly else 0.0

    quality = (
        await db.execute(
            text(_QUALITY_SQL), {"student_id": student_id, "since": since}
        )
    ).mappings().one()
    total_submissions = int(quality["total"] or 0)
    correct_ratio = (
        int(quality["correct"] or 0) / total_submissions
        if total_submissions >= MIN_QUALITY_SAMPLE
        else None
    )

    exam_day = exam_date_for(grade, moment.date())
    weeks_to_exam = max((exam_day - moment.date()).days, 1) / 7.0
    target = target_per_week_for(grade, moment.date())
    sprint = target == TARGET_PER_WEEK_EXAM_SPRINT and (
        grade is None or grade >= 11
    )

    #: Растим не быстрее, чем на GROWTH_FACTOR от нынешнего темпа, но не ниже
    #: минимума: у человека с нулевым темпом факт×1.2 = 0, и без пола он не
    #: получил бы ничего — то есть механика молчала бы ровно там, где она
    #: нужнее всего (18 из 60 за месяц не решили ни одного задания).
    raw = min(float(target), max(fact_per_week * GROWTH_FACTOR, float(MIN_PER_WEEK)))
    penalty = correct_ratio is not None and correct_ratio < QUALITY_THRESHOLD
    if penalty:
        raw *= QUALITY_PENALTY

    volume = int(round(max(min(raw, float(MAX_PER_WEEK)), float(MIN_PER_WEEK))))
    # Больше, чем осталось в программе, задать нельзя — иначе выдача попросит
    # то, чего нет, и пункты в ней окажутся невыполнимыми.
    volume = min(volume, remaining)

    #: Насколько человек не дотягивает до нормы своего класса. Считается по
    #: ФАКТУ, а не по выданному объёму: объём — это то, что мы задали, а
    #: отставание — то, что человек делает на самом деле.
    pace_gap = max(int(round(target - fact_per_week)), 0) if remaining > 0 else 0

    #: Насколько хватит программы при нынешней норме. Считается по НОРМЕ, а не
    #: по факту: вопрос «когда ученику станет нечего задавать», а не «когда он
    #: всё пройдёт».
    weeks_left = int(remaining // volume) if volume > 0 else None
    needs_more = remaining == 0 or (
        weeks_left is not None and weeks_left < PROGRAM_LOW_WEEKS
    )

    return VolumePlan(
        grade=int(grade) if grade is not None else None,
        grade_assumed=grade is None,
        exam_date=exam_day,
        weeks_to_exam=round(weeks_to_exam, 1),
        remaining_items=remaining,
        target_per_week=target,
        fact_per_week=round(fact_per_week, 1),
        correct_ratio=round(correct_ratio, 2) if correct_ratio is not None else None,
        quality_penalty_applied=penalty,
        volume_per_week=volume,
        weeks_of_program_left=weeks_left,
        needs_more_program=needs_more,
        exam_sprint=sprint,
        pace_gap=pace_gap,
    )


def volume_for_window(plan: VolumePlan, *, days: int) -> int:
    """Сколько элементов задать на промежуток в `days` дней.

    Выдача привязана не к неделе, а к следующему занятию: между занятиями
    может быть и три дня, и десять (у ученика с одним занятием в неделю и у
    ученика с двумя разный промежуток). Неделя — только единица нормы.

    Пол в один элемент: если до занятия остался день, задать «ноль» нельзя —
    выдача без состава бессмысленна.
    """
    if plan.volume_per_week <= 0:
        return 0
    scaled = plan.volume_per_week * max(days, 1) / 7.0
    return max(int(round(scaled)), 1)
