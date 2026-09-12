"""Посещаемость по факту: какие пропуски настоящие (tsk-914, tsk-916).

Статус участия ``no_show`` — гипотеза, а не факт. Он бывает призраком:

* ученик переехал в другой слот, старый ему выключили, но уже созданные
  занятия старого слота остались с ним участником — крон ставит ``no_show``
  каждую неделю (Курунов, 12.09: два «пропуска» четверга при двух
  отработанных субботних часах);
* преподаватель не отметил явку, а человек работал весь час;
* ученик пришёл на чужой час вместо своего, не оформляя перенос.

Правило оператора 12.09: «пропуск — только если он не погашен; фактически
был на другом часе — это тоже нужно отслеживать». Поэтому пропуск считается
ПО НЕДЕЛЕ: сколько часов положено по расписанию (активные слоты) против
сколько отработано — свои с отметкой «пришёл» плюс любой час школы, свой
или чужой, в окне которого ученик сдал или отметил не меньше
``LESSON_PRESENCE_MIN_ITEMS``. Непогашено только то, чего не хватает до
плана недели.

Это ОДИН предикат для всех, кто говорит слово «пропустил»: норма домашней
работы (нагон), серия пропусков в сводке занятия, посещаемость на дашборде
ученика и родителя, доска куратора. Пока каждый считал по-своему, один и тот
же человек в одном месте «пропустил 2», в другом «нагона нет»
(см. [[feedback_shared_predicate_must_be_called_not_copied]]).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    real_student_material_filter,
    real_student_results_filter,
)

#: Ученик занятие ПРОПУСТИЛ — не пришёл и ничего взамен не выбрал. Перенос
#: (``rescheduled``) и перерыв (``on_break``) пропуском не являются.
#: На проде 130 участий ``no_show`` и ни одного из них с переносом.
MISSED_STATUSES = ("no_show", "declined")

#: Со скольких сдач и отметок материалов внутри окна часа считаем, что человек
#: на нём был — независимо от отметки явки. Медиана по посещённым часам на
#: проде 12.09 — семь сдач за час; три — треть часа работы, случайно столько
#: не набирается.
LESSON_PRESENCE_MIN_ITEMS = 3

#: Посещаемость по КАЛЕНДАРНЫМ неделям, сразу для многих учеников.
#:
#: Недели берутся целиком (расписание недельное — план имеет смысл только для
#: полной недели), а пропуски считаются только внутри запрошенного окна:
#: иначе окно «последние 14 дней» уронило бы в счёт пропуск месячной давности
#: из той же календарной недели.
#:
#: Часы одного времени считаются один раз: в субботу в десять идут три группы,
#: а человек — один.
#:
#: Сдачи — только НАСТОЯЩИЕ (`real_student_results_filter`): три четверти
#: строк `task_results` на проде — ручные отметки преподавателя, и без фильтра
#: «был на часе» получал тот, кому в этот час проставили 208 зачётов руками
#: (Рузняева, 12.09, при `no_show`).
_WEEKLY_SQL = f"""
WITH students AS (
    SELECT unnest(CAST(:student_ids AS int[])) AS student_id
),
span AS (
    SELECT date_trunc('week', CAST(:since AS timestamptz)) AS from_at,
           date_trunc('week', CAST(:until AS timestamptz)) + interval '1 week' AS to_at
),
own AS (
    SELECT lop.student_id, lo.scheduled_at, lop.status,
           date_trunc('week', lo.scheduled_at) AS week
      FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
      JOIN students s ON s.student_id = lop.student_id
      CROSS JOIN span
     WHERE lo.scheduled_at >= span.from_at AND lo.scheduled_at < span.to_at
),
slots AS (
    SELECT DISTINCT lo.scheduled_at,
           lo.scheduled_at
             + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval) AS ends_at
      FROM lesson_occurrence lo
      CROSS JOIN span
     WHERE lo.scheduled_at >= span.from_at AND lo.scheduled_at < span.to_at
),
worked AS (
    SELECT x.student_id, x.scheduled_at
      FROM (
          SELECT tr.user_id AS student_id, sl.scheduled_at,
                 'task:' || tr.task_id AS item
            FROM task_results tr
            JOIN students s ON s.student_id = tr.user_id
            CROSS JOIN span
            JOIN slots sl
              ON tr.submitted_at >= sl.scheduled_at AND tr.submitted_at < sl.ends_at
           WHERE tr.submitted_at >= span.from_at AND tr.submitted_at < span.to_at
             AND {real_student_results_filter('tr')}
          UNION
          SELECT smp.student_id, sl.scheduled_at, 'material:' || smp.material_id
            FROM student_material_progress smp
            JOIN students s ON s.student_id = smp.student_id
            CROSS JOIN span
            JOIN slots sl
              ON smp.completed_at >= sl.scheduled_at AND smp.completed_at < sl.ends_at
           WHERE smp.status = 'completed'
             AND {real_student_material_filter('smp')}
             AND smp.completed_at >= span.from_at AND smp.completed_at < span.to_at
      ) x
     GROUP BY x.student_id, x.scheduled_at
    HAVING count(*) >= :min_items
),
hours AS (
    SELECT DISTINCT student_id, scheduled_at, date_trunc('week', scheduled_at) AS week
      FROM (
          SELECT student_id, scheduled_at FROM worked
          UNION
          SELECT student_id, scheduled_at FROM own WHERE status = 'confirmed'
      ) h
),
planned AS (
    SELECT lss.student_id, count(*) AS n
      FROM lesson_slot_student lss
      JOIN lesson_slot ls ON ls.id = lss.slot_id
      JOIN students s ON s.student_id = lss.student_id
     WHERE lss.is_active AND ls.is_active
     GROUP BY lss.student_id
),
weeks AS (
    SELECT student_id, week FROM own
    UNION
    SELECT student_id, week FROM hours
)
SELECT w.student_id, w.week,
       COALESCE(p.n, 0) AS planned,
       (SELECT count(*) FROM own o
         WHERE o.student_id = w.student_id AND o.week = w.week) AS own_hours,
       (SELECT count(*) FROM own o
         WHERE o.student_id = w.student_id AND o.week = w.week
           AND o.status = 'confirmed') AS attended_own,
       (SELECT count(*) FROM hours h
         WHERE h.student_id = w.student_id AND h.week = w.week) AS attended,
       (SELECT count(*) FROM own o
         WHERE o.student_id = w.student_id AND o.week = w.week
           AND o.status = ANY(:missed_statuses)
           AND o.scheduled_at >= :since AND o.scheduled_at <= :until) AS missed
  FROM weeks w
  LEFT JOIN planned p ON p.student_id = w.student_id
 ORDER BY w.student_id, w.week
"""

#: Свои занятия ученика в окне с признаком «работал в окне этого часа» —
#: чтобы разложить недельное «непогашено N» по конкретным занятиям.
_OWN_OCCURRENCES_SQL = f"""
SELECT lo.id, lo.scheduled_at, lop.status,
       date_trunc('week', lo.scheduled_at) AS week,
       (
           SELECT count(*) FROM (
               SELECT tr.task_id AS item FROM task_results tr
                WHERE tr.user_id = :student_id
                  AND {real_student_results_filter('tr')}
                  AND tr.submitted_at >= lo.scheduled_at
                  AND tr.submitted_at < lo.scheduled_at
                      + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval)
               UNION
               SELECT smp.material_id FROM student_material_progress smp
                WHERE smp.student_id = :student_id AND smp.status = 'completed'
                  AND {real_student_material_filter('smp')}
                  AND smp.completed_at >= lo.scheduled_at
                  AND smp.completed_at < lo.scheduled_at
                      + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval)
           ) w
       ) >= :min_items AS worked
  FROM lesson_occurrence_participant lop
  JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
 WHERE lop.student_id = :student_id
   AND lo.scheduled_at >= :since AND lo.scheduled_at <= :until
 ORDER BY lo.scheduled_at
"""


@dataclass(frozen=True)
class WeekAttendance:
    """Одна календарная неделя одного ученика."""

    student_id: int
    week: datetime
    #: Часов в неделю по расписанию (активные слоты). 0 — слотов нет.
    planned: int
    #: Своих занятий в неделе, любых статусов.
    own_hours: int
    #: Своих занятий с отметкой «пришёл».
    attended_own: int
    #: Отработанных часов: свои с отметкой плюс любые с работой в окне.
    attended: int
    #: Пропусков по статусу — внутри запрошенного окна.
    missed: int

    @property
    def unpaid(self) -> int:
        """Сколько пропусков недели не погашено.

        Не больше, чем не хватает до плана, и не больше самих пропусков: план
        два, отработал ноль, а занятие в неделе было одно (праздник) —
        непогашен один, а не два. Плана нет (ученик без слотов, разовые
        занятия) — планом считаются его собственные часы этой недели.
        """
        planned = self.planned or self.own_hours
        return min(self.missed, max(planned - self.attended, 0))


@dataclass(frozen=True)
class OccurrenceOutcome:
    """Одно занятие ученика глазами правила."""

    occurrence_id: int
    scheduled_at: datetime
    status: str
    #: Пропуск по статусу.
    missed: bool
    #: Пропуск погашен: работал в окне этого часа, либо неделя отработана.
    paid: bool

    @property
    def unpaid_miss(self) -> bool:
        return self.missed and not self.paid


async def weekly(
    db: AsyncSession,
    *,
    student_ids: Iterable[int],
    since: datetime,
    until: datetime,
) -> dict[int, list[WeekAttendance]]:
    """Посещаемость по неделям для нескольких учеников одним запросом."""
    ids = [int(i) for i in student_ids]
    if not ids:
        return {}
    rows = (
        await db.execute(
            text(_WEEKLY_SQL),
            {
                "student_ids": ids,
                "since": since,
                "until": until,
                "min_items": LESSON_PRESENCE_MIN_ITEMS,
                "missed_statuses": list(MISSED_STATUSES),
            },
        )
    ).mappings().all()
    result: dict[int, list[WeekAttendance]] = {i: [] for i in ids}
    for r in rows:
        result[int(r["student_id"])].append(
            WeekAttendance(
                student_id=int(r["student_id"]),
                week=r["week"],
                planned=int(r["planned"] or 0),
                own_hours=int(r["own_hours"] or 0),
                attended_own=int(r["attended_own"] or 0),
                attended=int(r["attended"] or 0),
                missed=int(r["missed"] or 0),
            )
        )
    return result


def totals(weeks: Iterable[WeekAttendance]) -> tuple[int, int]:
    """(пропусков по статусу, из них непогашенных) за все недели."""
    missed = unpaid = 0
    for w in weeks:
        missed += w.missed
        unpaid += w.unpaid
    return missed, unpaid


async def unpaid_missed(
    db: AsyncSession,
    *,
    student_ids: Iterable[int],
    since: datetime,
    until: datetime,
) -> dict[int, tuple[int, int]]:
    """{ученик: (пропусков, непогашенных)} — для списков и досок."""
    by_student = await weekly(db, student_ids=student_ids, since=since, until=until)
    return {sid: totals(weeks) for sid, weeks in by_student.items()}


async def outcomes(
    db: AsyncSession,
    *,
    student_id: int,
    since: datetime,
    until: datetime,
) -> list[OccurrenceOutcome]:
    """Свои занятия ученика в окне, по времени, с признаком «настоящий пропуск».

    Недельное «непогашено N» раскладывается по занятиям так: пропуски, в окне
    которых человек работал, погашены сами по себе; из остальных непогашенными
    считаются N ПОСЛЕДНИХ в неделе. Порядок — допущение (кто именно из двух
    пропусков «отработан», данные не говорят), выбран в пользу серии: серия
    считается с конца, и последний пропуск остаётся на виду.
    """
    weeks = (await weekly(db, student_ids=[student_id], since=since, until=until)).get(
        student_id, []
    )
    unpaid_by_week = {w.week: w.unpaid for w in weeks}
    rows = (
        await db.execute(
            text(_OWN_OCCURRENCES_SQL),
            {
                "student_id": student_id,
                "since": since,
                "until": until,
                "min_items": LESSON_PRESENCE_MIN_ITEMS,
            },
        )
    ).mappings().all()

    # Сначала — пропуски без работы в окне, по неделям, поздние первыми.
    pending: dict[Any, list[int]] = {}
    for r in rows:
        if r["status"] in MISSED_STATUSES and not r["worked"]:
            pending.setdefault(r["week"], []).append(int(r["id"]))
    unpaid_ids: set[int] = set()
    for week, ids in pending.items():
        take = unpaid_by_week.get(week, len(ids))
        unpaid_ids.update(ids[::-1][:take])

    return [
        OccurrenceOutcome(
            occurrence_id=int(r["id"]),
            scheduled_at=r["scheduled_at"],
            status=str(r["status"]),
            missed=r["status"] in MISSED_STATUSES,
            paid=(r["status"] in MISSED_STATUSES) and int(r["id"]) not in unpaid_ids,
        )
        for r in rows
    ]


def streak(outcomes_: list[OccurrenceOutcome]) -> int:
    """Серия НЕПОГАШЕННЫХ пропусков подряд, считая с последнего занятия.

    Погашенный пропуск рвёт серию так же, как явка: человек на той неделе
    отработал своё, и «пропустил подряд» про него — неправда.
    """
    n = 0
    for outcome in reversed(outcomes_):
        if not outcome.unpaid_miss:
            break
        n += 1
    return n


def occurrence_paid(outcomes_: list[OccurrenceOutcome], occurrence_id: int) -> Optional[bool]:
    """Погашен ли конкретный пропуск; None — занятия в списке нет."""
    for o in outcomes_:
        if o.occurrence_id == occurrence_id:
            return o.paid if o.missed else None
    return None
