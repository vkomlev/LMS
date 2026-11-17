"""tsk-1516: дашборд методиста по ученикам — посещаемость, трудности, скорость,
дисциплина ДЗ за месяц/год/произвольный период.

Формулы зафиксированы в задаче tsk-1516 (Root). Источники read-only:
``lesson_occurrence_participant`` (посещаемость), ``help_requests`` +
``learning_events.event_type='hint_open'`` + ``task_results`` (трудности,
скорость), ``learning_time_session`` (время на задание), ``homework_assignment`` /
``homework_item`` (дисциплина ДЗ). Просмотр подсказок учеником фиксируется НЕ в
``task_hint_drafts`` (это черновики методиста), а в ``learning_events``
(событие ``hint_open`` — тот же источник, что в ``task_results_service``).

Отклонения: к прошлому периоду той же длительности и к среднему по группе
(группа = ученики с любой активностью за текущий период).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MSK = ZoneInfo("Europe/Moscow")

# Вердикт темпа: пороги относительно прошлого периода.
_PACE_FALLING = 0.8
_PACE_ACCELERATING = 1.2


def _msk_bounds(
    from_date: str, to_date: str,
) -> tuple[datetime, datetime, datetime, datetime]:
    """[from, to] по МСК (to включительно) → UTC-границы текущего и прошлого
    периода той же длительности."""
    d_from = datetime.strptime(from_date, "%Y-%m-%d").date()
    d_to = datetime.strptime(to_date, "%Y-%m-%d").date()
    if d_to < d_from:
        raise ValueError("to must be >= from")
    days = (d_to - d_from).days + 1
    if days > 366:
        raise ValueError("period must be at most 366 days")
    cur_from = datetime.combine(d_from, datetime.min.time(), tzinfo=MSK)
    cur_to = datetime.combine(d_to, datetime.min.time(), tzinfo=MSK) + timedelta(days=1)
    prev_to = cur_from
    prev_from = cur_from - timedelta(days=days)
    return cur_from, cur_to, prev_from, prev_to


# Формула посещаемости (tsk-1533, решение зафиксировано в задаче): платформа
# фиксирует явку статусом 'confirmed' ('completed' — редкий ручной вариант,
# считаем тоже посещением). Знаменатель — confirmed+completed+no_show;
# 'scheduled' (будущие, tsk-1516) и 'rescheduled' (перенесённое ≠ пропуск,
# tsk-1533) в знаменатель НЕ входят.
_ATTENDANCE_SQL = text("""
    SELECT p.student_id,
           COUNT(*) AS planned,
           COUNT(*) FILTER (WHERE p.status IN ('confirmed', 'completed')) AS attended
    FROM lesson_occurrence_participant p
    JOIN lesson_occurrence o ON o.id = p.occurrence_id
    WHERE o.scheduled_at >= :from_ts AND o.scheduled_at < :to_ts
      AND p.status IN ('confirmed', 'completed', 'no_show')
    GROUP BY p.student_id
""")

# Последняя активность на платформе (tsk-1533) — без привязки к периоду:
# max(submitted_at решений, created_at обращений, окончание учебных сессий).
_LAST_ACTIVITY_SQL = text("""
    SELECT student_id, MAX(at) AS last_activity_at
    FROM (
        SELECT user_id AS student_id, MAX(submitted_at) AS at
        FROM task_results
        GROUP BY user_id
        UNION ALL
        SELECT student_id, MAX(created_at)
        FROM help_requests
        GROUP BY student_id
        UNION ALL
        SELECT student_id, MAX(COALESCE(ended_at, started_at))
        FROM learning_time_session
        GROUP BY student_id
    ) sources
    GROUP BY student_id
""")

_TASKS_SQL = text("""
    WITH first_correct AS (
        SELECT DISTINCT ON (r.user_id, r.task_id)
               r.user_id, r.task_id, r.count_retry
        FROM task_results r
        WHERE r.submitted_at >= :from_ts AND r.submitted_at < :to_ts
          AND r.is_correct IS TRUE
        ORDER BY r.user_id, r.task_id, r.submitted_at
    )
    SELECT user_id AS student_id,
           COUNT(*) AS solved,
           COUNT(*) FILTER (WHERE count_retry = 0) AS solved_first_try
    FROM first_correct
    GROUP BY user_id
""")

_HELP_SQL = text("""
    SELECT student_id, COUNT(*) AS help_requests
    FROM help_requests
    WHERE created_at >= :from_ts AND created_at < :to_ts
    GROUP BY student_id
""")

_HINTS_SQL = text("""
    SELECT student_id, COUNT(*) AS hint_opens
    FROM learning_events
    WHERE event_type = 'hint_open'
      AND created_at >= :from_ts AND created_at < :to_ts
    GROUP BY student_id
""")

_TIME_SQL = text("""
    SELECT student_id,
           AVG(seconds)::float AS avg_task_seconds
    FROM learning_time_session
    WHERE item_type = 'task'
      AND started_at >= :from_ts AND started_at < :to_ts
    GROUP BY student_id
""")

_HOMEWORK_SQL = text("""
    SELECT a.student_id,
           COUNT(i.id) AS assigned,
           COUNT(i.id) FILTER (
               WHERE EXISTS (
                     SELECT 1 FROM task_results r
                     WHERE r.user_id = a.student_id
                       AND r.task_id = i.task_id
                       AND r.is_correct IS TRUE
                       AND r.submitted_at >= a.issued_at
                       AND r.submitted_at <= a.due_at
                 )
           ) AS done_on_time
    FROM homework_assignment a
    JOIN homework_item i ON i.homework_id = a.id
                          AND i.kind = 'task'
                          AND i.tier = 'required'
    WHERE a.cancelled_at IS NULL
      AND a.issued_at >= :from_ts AND a.issued_at < :to_ts
    GROUP BY a.student_id
""")


def _rate(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if den is None or den == 0:
        return None
    return round(num / den, 4)


def _pace_verdict(current: Optional[float], previous: Optional[float]) -> Optional[str]:
    if current is None or previous is None or previous == 0:
        return None
    ratio = current / previous
    if ratio < _PACE_FALLING:
        return "falling"
    if ratio > _PACE_ACCELERATING:
        return "accelerating"
    return "on_track"


async def _period_rows(db: AsyncSession, sql: Any, params: dict) -> dict[int, Any]:
    rows = (await db.execute(sql, params)).fetchall()
    return {int(r.student_id): r for r in rows}


async def get_students_dashboard(
    db: AsyncSession, *, from_date: str, to_date: str,
    student_id: Optional[int] = None,
) -> dict[str, Any]:
    """Агрегаты по ученикам за период + прошлый период + среднее по группе."""
    cur_from, cur_to, prev_from, prev_to = _msk_bounds(from_date, to_date)
    cur = {"from_ts": cur_from, "to_ts": cur_to}
    prev = {"from_ts": prev_from, "to_ts": prev_to}

    att_c = await _period_rows(db, _ATTENDANCE_SQL, cur)
    att_p = await _period_rows(db, _ATTENDANCE_SQL, prev)
    tasks_c = await _period_rows(db, _TASKS_SQL, cur)
    tasks_p = await _period_rows(db, _TASKS_SQL, prev)
    help_c = await _period_rows(db, _HELP_SQL, cur)
    help_p = await _period_rows(db, _HELP_SQL, prev)
    hints_c = await _period_rows(db, _HINTS_SQL, cur)
    hints_p = await _period_rows(db, _HINTS_SQL, prev)
    time_c = await _period_rows(db, _TIME_SQL, cur)
    hw_c = await _period_rows(db, _HOMEWORK_SQL, cur)
    hw_p = await _period_rows(db, _HOMEWORK_SQL, prev)

    last_activity = await _period_rows(db, _LAST_ACTIVITY_SQL, {})

    # Группа — ученики с активностью за текущий ИЛИ прошлый период: иначе
    # резко упавший «до нуля» ученик (самый важный случай для вердикта
    # falling) выпадал бы из дашборда вместе со сравнением.
    student_ids = (
        set(att_c) | set(att_p)
        | set(tasks_c) | set(tasks_p)
        | set(help_c) | set(help_p)
        | set(hints_c) | set(hints_p)
        | set(hw_c) | set(hw_p)
        | set(time_c)
    )
    if student_id is not None:
        student_ids = {sid for sid in student_ids if sid == student_id}

    names: dict[int, Optional[str]] = {}
    if student_ids:
        rows = (
            await db.execute(
                text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
                {"ids": list(student_ids)},
            )
        ).fetchall()
        names = {int(r.id): r.full_name for r in rows}

    weeks = max((cur_to - cur_from).total_seconds() / (7 * 86400), 1 / 7)

    def _row(sid: int) -> dict[str, Any]:
        a_c, a_p = att_c.get(sid), att_p.get(sid)
        t_c, t_p = tasks_c.get(sid), tasks_p.get(sid)
        h_c, h_p = help_c.get(sid), help_p.get(sid)
        n_c, n_p = hints_c.get(sid), hints_p.get(sid)
        tm_c = time_c.get(sid)
        w_c, w_p = hw_c.get(sid), hw_p.get(sid)

        att_rate = _rate(
            a_c.attended if a_c else None, a_c.planned if a_c else None,
        )
        att_prev = _rate(
            a_p.attended if a_p else None, a_p.planned if a_p else None,
        )
        first_try = _rate(
            t_c.solved_first_try if t_c else None, t_c.solved if t_c else None,
        )
        first_try_prev = _rate(
            t_p.solved_first_try if t_p else None, t_p.solved if t_p else None,
        )
        solved_per_week = (
            round(t_c.solved / weeks, 2) if t_c and t_c.solved else None
        )
        solved_per_week_prev = (
            round(t_p.solved / weeks, 2) if t_p and t_p.solved else None
        )
        hw_rate = _rate(
            w_c.done_on_time if w_c else None, w_c.assigned if w_c else None,
        )
        hw_rate_prev = _rate(
            w_p.done_on_time if w_p else None, w_p.assigned if w_p else None,
        )

        return {
            "student_id": sid,
            "full_name": names.get(sid),
            "last_activity_at": (
                la.last_activity_at.isoformat()
                if (la := last_activity.get(sid)) is not None
                and la.last_activity_at is not None
                else None
            ),
            "attendance": {
                "planned": int(a_c.planned) if a_c else 0,
                "attended": int(a_c.attended) if a_c else 0,
                "rate": att_rate,
                "prev_rate": att_prev,
            },
            "difficulty": {
                "help_requests": int(h_c.help_requests) if h_c else 0,
                "help_requests_prev": int(h_p.help_requests) if h_p else 0,
                "hint_opens": int(n_c.hint_opens) if n_c else 0,
                "hint_opens_prev": int(n_p.hint_opens) if n_p else 0,
                "solved": int(t_c.solved) if t_c else 0,
                "solved_first_try": int(t_c.solved_first_try) if t_c else 0,
                "first_try_rate": first_try,
                "first_try_rate_prev": first_try_prev,
            },
            "pace": {
                "avg_task_seconds": (
                    round(tm_c.avg_task_seconds, 1) if tm_c else None
                ),
                "solved_per_week": solved_per_week,
                "solved_per_week_prev": solved_per_week_prev,
                "verdict": _pace_verdict(solved_per_week, solved_per_week_prev),
            },
            "homework": {
                "assigned": int(w_c.assigned) if w_c else 0,
                "done_on_time": int(w_c.done_on_time) if w_c else 0,
                "on_time_rate": hw_rate,
                "on_time_rate_prev": hw_rate_prev,
            },
        }

    students = [_row(sid) for sid in sorted(student_ids)]

    def _avg(key_fn: Any) -> Optional[float]:
        vals = [key_fn(s) for s in students]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    group = {
        "attendance_rate": _avg(lambda s: s["attendance"]["rate"]),
        "first_try_rate": _avg(lambda s: s["difficulty"]["first_try_rate"]),
        "on_time_rate": _avg(lambda s: s["homework"]["on_time_rate"]),
        "solved_per_week": _avg(lambda s: s["pace"]["solved_per_week"]),
        "help_requests": _avg(lambda s: s["difficulty"]["help_requests"]),
        "hint_opens": _avg(lambda s: s["difficulty"]["hint_opens"]),
    }

    return {
        "from": from_date,
        "to": to_date,
        "prev_from": prev_from.astimezone(MSK).date().isoformat(),
        "prev_to": (prev_to.astimezone(MSK).date() - timedelta(days=1)).isoformat(),
        "students": students,
        "group_average": group,
    }
