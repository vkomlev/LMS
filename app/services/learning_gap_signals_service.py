"""Сигналы «нужно повторение»: теме — методисту, ученику — преподавателю.

tsk-572, фаза 7. Отдельно от `learning_gaps_service` намеренно: тот считает
цифры, этот управляет тем, что с цифрами делают люди.

**Почему адресата два.** Датчик замечает две разные вещи. Проваливается ТЕМА
(много учеников, высокая доля ошибок) — это работа с контентом, заявка методисту
на мини-курс. Буксует КОНКРЕТНЫЙ ученик — это сигнал преподавателю: он ведёт
занятия и видит ученика живьём, а методист нет.

Смешать потоки нельзя: методисту незачем разбирать личные затыки, а
преподавателю — получать заявки на переписывание курса.

**Зачем комментарий преподавателя.** Он видел ученика вживую и знает то, чего в
долях ошибок нет: «болел две недели», «путает ввод и вывод, а не циклы». Этот
комментарий уезжает вместе с эскалацией методисту и часто ценнее самой цифры.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.learning_gaps_service import (
    find_topic_gaps,
    real_student_results_filter,
)

logger = logging.getLogger(__name__)

# Пороги для одного ученика отдельные от тем: у человека выборка всегда меньше,
# и требовать от неё той же статистики бессмысленно.
STUDENT_MIN_SUBMISSIONS = 8
STUDENT_ERROR_RATE_THRESHOLD = 0.5

_STUDENT_GAPS_SQL = """
SELECT tr.user_id AS student_id,
       t.course_id,
       c.title AS course_title,
       COUNT(*) AS submissions,
       COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) AS wrong_rate
FROM task_results tr
JOIN tasks t ON t.id = tr.task_id AND t.is_active
JOIN courses c ON c.id = t.course_id
WHERE {real_student}
  AND tr.received_at > now() - make_interval(days => :days)
GROUP BY tr.user_id, t.course_id, c.title
HAVING COUNT(*) >= :min_submissions
   AND COUNT(*) FILTER (WHERE tr.is_correct IS FALSE)::float / COUNT(*) >= :threshold
ORDER BY wrong_rate DESC
LIMIT :limit
"""


async def find_student_gaps(
    db: AsyncSession,
    *,
    days: int = 30,
    min_submissions: int = STUDENT_MIN_SUBMISSIONS,
    threshold: float = STUDENT_ERROR_RATE_THRESHOLD,
    limit: int = 100,
) -> list[dict]:
    """Ученики, которым нужно повторение конкретной темы.

    Тот же фильтр источника, что и у тем: ручная простановка преподавателя — не
    ответ ученика и в счёт его ошибок идти не должна.
    """
    sql = _STUDENT_GAPS_SQL.format(real_student=real_student_results_filter("tr"))
    rows = (await db.execute(text(sql), {
        "days": days, "min_submissions": min_submissions,
        "threshold": threshold, "limit": limit,
    })).mappings().all()
    return [dict(r) for r in rows]


async def upsert_signal(
    db: AsyncSession, *, course_id: int, student_id: int | None,
    submissions: int, students: int, wrong_rate: float,
) -> int | None:
    """Завести сигнал, если открытого такого ещё нет.

    Повтор молча пропускается: cron ходит по расписанию, и без этого за неделю
    накопилось бы семь одинаковых записей — верный способ отучить людей их
    читать. Единственность держит частичный уникальный индекс в БД.
    """
    res = await db.execute(text("""
        INSERT INTO learning_gap_signal
            (course_id, student_id, submissions, students, wrong_rate, status)
        VALUES (:cid, :sid, :subs, :studs, :rate, 'new')
        ON CONFLICT DO NOTHING
        RETURNING id
    """), {"cid": course_id, "sid": student_id, "subs": submissions,
           "studs": students, "rate": wrong_rate})
    row = res.first()
    return int(row[0]) if row else None


async def scan_and_create_signals(db: AsyncSession, *, days: int = 30) -> dict:
    """Полный проход датчика: темы и ученики. Вызывается по расписанию.

    Итог пишется в лог ВСЕГДА, даже когда сигналов ноль: молчащий cron
    неотличим от отсутствующего, и именно так молчаливый отказ живёт годами.
    """
    topics = await find_topic_gaps(db, days=days)
    students = await find_student_gaps(db, days=days)

    new_topics = 0
    for g in topics:
        if await upsert_signal(
            db, course_id=g.course_id, student_id=None,
            submissions=g.submissions, students=g.students, wrong_rate=g.wrong_rate,
        ):
            new_topics += 1

    new_students = 0
    for r in students:
        if await upsert_signal(
            db, course_id=int(r["course_id"]), student_id=int(r["student_id"]),
            submissions=int(r["submissions"]), students=1,
            wrong_rate=float(r["wrong_rate"]),
        ):
            new_students += 1

    await db.commit()
    logger.info(
        "learning_gaps: проход завершён — тем найдено %s (новых сигналов %s), "
        "учеников %s (новых %s), период %s дн.",
        len(topics), new_topics, len(students), new_students, days,
    )
    return {
        "topics_found": len(topics), "topic_signals_created": new_topics,
        "students_found": len(students), "student_signals_created": new_students,
    }


async def acknowledge_signal(
    db: AsyncSession, *, signal_id: int, teacher_id: int,
    comment: str | None = None, escalate: bool = False,
) -> bool:
    """Преподаватель принял сигнал к сведению.

    `escalate=False` — «принял, разберусь сам на занятии». Это нормальный исход,
    а не бездействие: у преподавателя есть живой канал, которого у методиста нет.
    `escalate=True` — уходит методисту вместе с комментарием.
    """
    status = "escalated" if escalate else "acknowledged"
    # Признак передаётся отдельным булевым параметром, а не сравнением `:st`
    # с литералом внутри CASE: драйвер не может вывести тип параметра в таком
    # сравнении и роняет транзакцию целиком, а не только этот запрос.
    res = await db.execute(text("""
        UPDATE learning_gap_signal
        SET status = :st,
            teacher_id = :tid,
            teacher_comment = COALESCE(:comment, teacher_comment),
            acknowledged_at = COALESCE(acknowledged_at, now()),
            escalated_at = CASE WHEN :is_escalation THEN now() ELSE escalated_at END
        WHERE id = :sid AND status IN ('new', 'acknowledged')
        RETURNING id
    """), {"sid": signal_id, "tid": teacher_id, "comment": comment,
           "st": status, "is_escalation": escalate})
    ok = res.first() is not None
    if ok:
        await db.commit()
    return ok


async def dismiss_signal(
    db: AsyncSession, *, signal_id: int, teacher_id: int, comment: str | None = None
) -> bool:
    """Отклонить сигнал: повторение не нужно.

    Отклонённые — не мусор. По ним видно, что датчик шумит (ученик болел,
    задание сломано, ошибка в эталоне), и это основание пересмотреть пороги, а
    не молча терпеть ложные срабатывания.
    """
    res = await db.execute(text("""
        UPDATE learning_gap_signal
        SET status = 'dismissed', teacher_id = :tid,
            teacher_comment = COALESCE(:comment, teacher_comment),
            acknowledged_at = COALESCE(acknowledged_at, now())
        WHERE id = :sid AND status IN ('new', 'acknowledged')
        RETURNING id
    """), {"sid": signal_id, "tid": teacher_id, "comment": comment})
    ok = res.first() is not None
    if ok:
        await db.commit()
    return ok


async def list_signals(
    db: AsyncSession, *, for_student: bool | None = None,
    statuses: tuple[str, ...] = ("new", "acknowledged"), limit: int = 50,
) -> list[dict]:
    """Сигналы для показа.

    `for_student=True` — ученические (преподавателю), `False` — темы
    (методисту), `None` — все.
    """
    where = ["s.status = ANY(:statuses)"]
    if for_student is True:
        where.append("s.student_id IS NOT NULL")
    elif for_student is False:
        where.append("s.student_id IS NULL")
    clause = " AND ".join(where)
    rows = (await db.execute(text(f"""
        SELECT s.id, s.course_id, c.title AS course_title, s.student_id,
               u.full_name AS student_name, s.submissions, s.students,
               s.wrong_rate, s.status, s.teacher_comment, s.created_at
        FROM learning_gap_signal s
        JOIN courses c ON c.id = s.course_id
        LEFT JOIN users u ON u.id = s.student_id
        WHERE {clause}
        ORDER BY s.wrong_rate DESC, s.created_at DESC
        LIMIT :limit
    """), {"statuses": list(statuses), "limit": limit})).mappings().all()
    return [dict(r) for r in rows]
