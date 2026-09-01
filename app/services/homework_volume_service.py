"""Сколько задавать на дом: норма из темпа ученика и срока до экзамена (tsk-741).

Формула согласована с оператором 01.09.2026 и построена на замере боевой базы:
у 60 учеников курсов ЕГЭ медиана — 35 верных сдач за 4 недели (≈9 в неделю),
p90 — 122 (≈30), 18 человек не решили за месяц ничего. При этом одиннадцати-
класснику, чтобы пройти 797 заданий до июня 2027, нужно ≈20 в неделю. Разрыв
между «надо» и «делает» — это и есть то, что система обязана показывать.

    надо   = остаток программы / (недель до экзамена × 0.85)
    факт   = медиана завершённых элементов за 3 полные недели
    объём  = clamp( min(надо, факт × 1.2), 3, 25 )

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
  ступеньками. Если так программа не успевает к экзамену — это не повод
  завалить ученика, а сигнал преподавателю: он виден в `weeks_behind`.
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

logger = logging.getLogger(__name__)

#: Меньше этого на дом не задаём — иначе выдача теряет смысл.
MIN_PER_WEEK = 3
#: Больше этого не задаём никогда: выше p90 нынешнего темпа ДЗ просто
#: перестают делать, и невыполнимая норма обесценивает саму механику.
MAX_PER_WEEK = 25
#: На сколько норма может превышать сегодняшний темп ученика за один шаг.
GROWTH_FACTOR = 1.2
#: Запас программы на повторение, болезни и каникулы.
SCHEDULE_RESERVE = 0.85
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
    #: Незавершённых элементов программы.
    remaining_items: int
    #: Сколько нужно в неделю, чтобы успеть.
    need_per_week: float
    #: Сколько человек делает сейчас (медиана за FACT_WEEKS недель).
    fact_per_week: float
    #: Доля верных сдач за то же окно; None — сдач слишком мало.
    correct_ratio: Optional[float]
    #: True — объём уменьшен из-за низкой доли верных.
    quality_penalty_applied: bool
    #: Итоговая норма на неделю.
    volume_per_week: int
    #: На сколько недель опаздывает программа при нынешнем темпе; 0 — успевает.
    weeks_behind: int

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
),
course_materials AS (
    SELECT DISTINCT m.id
      FROM materials m JOIN tree ON tree.member_course_id = m.course_id
     WHERE COALESCE(m.is_active, true)
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
        await db.execute(text(_REMAINING_SQL), {"student_id": student_id})
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
    need_per_week = remaining / (weeks_to_exam * SCHEDULE_RESERVE) if remaining else 0.0

    #: Растим не быстрее, чем на GROWTH_FACTOR от нынешнего темпа, но не ниже
    #: минимума: у человека с нулевым темпом факт×1.2 = 0, и без пола он не
    #: получил бы ничего — то есть механика молчала бы ровно там, где она
    #: нужнее всего (18 из 60 за месяц не решили ни одного задания).
    raw = min(need_per_week, max(fact_per_week * GROWTH_FACTOR, float(MIN_PER_WEEK)))
    penalty = correct_ratio is not None and correct_ratio < QUALITY_THRESHOLD
    if penalty:
        raw *= QUALITY_PENALTY

    volume = int(round(max(min(raw, float(MAX_PER_WEEK)), float(MIN_PER_WEEK))))
    if remaining == 0:
        volume = 0

    #: На сколько недель программа опаздывает при нынешнем темпе. Считается по
    #: ФАКТУ, а не по норме: норма — это то, что мы задали, а опоздание — то,
    #: что будет, если человек продолжит как сейчас.
    weeks_behind = 0
    if remaining > 0:
        if fact_per_week <= 0:
            weeks_behind = int(round(weeks_to_exam))
        else:
            weeks_needed = remaining / fact_per_week
            weeks_behind = max(int(round(weeks_needed - weeks_to_exam)), 0)

    return VolumePlan(
        grade=int(grade) if grade is not None else None,
        grade_assumed=grade is None,
        exam_date=exam_day,
        weeks_to_exam=round(weeks_to_exam, 1),
        remaining_items=remaining,
        need_per_week=round(need_per_week, 1),
        fact_per_week=round(fact_per_week, 1),
        correct_ratio=round(correct_ratio, 2) if correct_ratio is not None else None,
        quality_penalty_applied=penalty,
        volume_per_week=volume,
        weeks_behind=weeks_behind,
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
