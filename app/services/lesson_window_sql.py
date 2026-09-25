"""Общий SQL-предикат «эта работа сделана на уроке» (tsk-1041, tsk-1111).

Один строитель на все выборки: состав ДЗ ученика, сводки преподавателя и
родителя, доля «на занятиях сделано N%». Копии уже разъезжались — одна не
исключала перенесённые занятия, другая требовала подтверждённую явку
(см. [[feedback_shared_predicate_must_be_called_not_copied]]).

Правило:
- окно урока — строго [scheduled_at, scheduled_at + duration]. Запаса ДО
  начала нет: сданное до звонка — всегда домашняя работа, даже если та же
  сдача отметила явку автоматически (`lesson_auto_confirm_early_grace_minutes`
  относится только к явке);
- урок в счёт, только если ученик на нём не отмечен отсутствующим:
  `scheduled` (урок ещё идёт, явку не отметили), `confirmed` или `completed`. Решал в час
  урока, на котором его не было (`no_show`, `declined`), или урок перенесён
  (`rescheduled`) — это работа дома. Прошедший урок, по которому неявку так
  и не отметили (остался `scheduled`), считается посещённым: доказать
  обратное нечем.

Окно закрытое с обеих сторон (`<=` конца). У `attendance_service`
(«работал ли в этот конкретный час») оно полуоткрытое — это другой вопрос,
про явку на одно занятие, а не про род работы.
"""

from __future__ import annotations

#: Статусы участия, при которых время урока — время урока для этого ученика.
LESSON_PRESENT_STATUSES_SQL = "('scheduled', 'confirmed', 'completed')"


def in_lesson_sql(ts: str, sid: str) -> str:
    """SQL-условие: момент `ts` попал в окно занятия, на котором был ученик `sid`.

    `ts` и `sid` — SQL-выражения (колонка или bind-параметр), не значения.
    """
    return (
        "EXISTS (SELECT 1 FROM lesson_occurrence_participant lop "
        "  JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id "
        f" WHERE lop.student_id = {sid} "
        f"   AND lop.status IN {LESSON_PRESENT_STATUSES_SQL} "
        f"   AND {ts} >= lo.scheduled_at "
        f"   AND {ts} <= lo.scheduled_at "
        "       + CAST(COALESCE(lo.duration_minutes, 60) || ' minutes' AS interval))"
    )
