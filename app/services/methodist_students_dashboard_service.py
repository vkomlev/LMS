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

import asyncio
import logging
import time as time_module
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

MSK = ZoneInfo("Europe/Moscow")

logger = logging.getLogger(__name__)

# Вердикт темпа: пороги относительно прошлого периода.
_PACE_FALLING = 0.8
_PACE_ACCELERATING = 1.2

# tsk-1536: кэш вердиктов «успевает ли к экзамену» в памяти процесса. Расчёт
# тяжёлый (~0.5 с на ученика, tsk-815): при ~100 программных учениках первый
# прогон дашборда идёт секунды, без кэша каждое обновление страницы методистом
# повторяло бы всю работу. Вердикт меняется медленно (недельный темп), 20 минут
# расхождения — внутри погрешности самой метрики. Ключ — (student_id, дата):
# вердикт считается «на сегодня», после полуночи кэш вчерашнего дня не
# используется. Протухшие записи отдаём как есть и пересчитываем в фоне,
# чтобы первый запрос после истечения TTL не ждал ~60 с пересчёта всей
# программной cohortы.
_PROGRAM_PACE_CACHE_TTL_SEC = 20 * 60
_program_pace_cache: dict[
    tuple[int, str], tuple[float, Optional[dict[str, Any]]],
] = {}
# Ключи, по которым фоновый пересчёт уже запущен (не плодим задачи).
_program_pace_refreshing: set[tuple[int, str]] = set()
# Держим ссылки на фоновые задачи, чтобы их не собрал GC.
_program_pace_refresh_tasks: set[asyncio.Task] = set()


def _clear_program_pace_cache() -> None:
    """Сброс кэша вердиктов (тесты)."""
    _program_pace_cache.clear()
    _program_pace_refreshing.clear()
    for task in _program_pace_refresh_tasks:
        task.cancel()
    _program_pace_refresh_tasks.clear()


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
# Фильтр по списку студентов дашборда нужен, чтобы не сканировать всю историю
# трёх таблиц.
_LAST_ACTIVITY_SQL = text("""
    SELECT student_id, MAX(at) AS last_activity_at
    FROM (
        SELECT user_id AS student_id, submitted_at AS at
        FROM task_results
        WHERE user_id = ANY(:ids)
        UNION ALL
        SELECT student_id, created_at
        FROM help_requests
        WHERE student_id = ANY(:ids)
        UNION ALL
        SELECT student_id, COALESCE(ended_at, started_at)
        FROM learning_time_session
        WHERE student_id = ANY(:ids)
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

# tsk-1537: авто-архив недельного ДЗ отменяет назначение ровно в срок
# (cancelled_at = due_at); старый фильтр `cancelled_at IS NULL` стирал всю
# историю авто-выдач (дашборд показывал 0/0). Правило: назначение считается,
# если оно активно ИЛИ отменено не раньше срока (отмена = конец жизненного
# цикла, а не досрочная ревокация). Досрочно отменённые (перепланирование)
# не считаются. Разведка по базе: 75% cancelled — жизненный цикл, 25% —
# досрочные ревокации.
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
    WHERE (a.cancelled_at IS NULL OR a.cancelled_at >= a.due_at)
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


# tsk-1535: планы, которые методист прячет галочкой «убрать выпускников и
# тариф Тест» — subscription_plan.id 1 «Test» и 9 «Выпускник».
# Исключение — АКТИВНАЯ подписка (ends_on IS NULL): ушедший из школы
# «Выпускник» с закончившейся подпиской не считается.
_EXCLUDED_PLAN_IDS = (1, 9)

_EXCLUDED_PLANS_SQL = text("""
    SELECT s.student_id
    FROM student_subscription s
    WHERE s.ends_on IS NULL
      AND s.plan_id = ANY(:plan_ids)
      AND s.student_id = ANY(:ids)
""")

# tsk-1536: «успевает ли к экзамену» — программные курсы учеников одним
# запросом, чтобы не гонять per-student расчёт по всему дашборду: считается
# он только для записанных на программу ЕГЭ/ОГЭ, а таких обычно меньшинство.
_PROGRAM_ENROLLMENT_SQL = text("""
    SELECT user_id AS student_id, course_id
    FROM user_courses
    WHERE user_id = ANY(:ids) AND is_active = true
""")

# Разрыв «нужно минус делает» в минутах, ниже которого человек считается
# идущим по плану. Тот же порог, что в сводке преподавателя
# (SPW components/teacher/student-verdict.ts, PACE_GAP_MINUTES_THRESHOLD):
# разница в паре минут внутри погрешности веса, а выглядит как замечание.
_ON_TRACK_GAP_MINUTES = 10


def _program_on_track(plan: Any) -> Optional[bool]:
    """Успевает ли к сроку — правило сводки преподавателя, не своё.

    Минутная шкала (когда вес измерен) с порогом `_ON_TRACK_GAP_MINUTES`;
    штучная — запасная, без порога: там разрыв целочисленный и «не дотягивает
    1» уже значимо. Данных нет (нет и штучной пары) — None, «нет данных».
    """
    if (
        plan.target_minutes_per_week is not None
        and plan.fact_minutes_per_week is not None
    ):
        return (
            plan.target_minutes_per_week - plan.fact_minutes_per_week
            < _ON_TRACK_GAP_MINUTES
        )
    if plan.target_per_week is not None and plan.fact_per_week is not None:
        return plan.fact_per_week >= plan.target_per_week
    return None


async def _compute_program_pace(
    session: AsyncSession, sid: int, now: datetime,
) -> tuple[int, Optional[dict[str, Any]]]:
    # Колонка необязательная: ошибка расчёта по одному ученику не
    # должна ронять весь дашборд — у него просто не будет вердикта.
    from app.services import homework_volume_service

    try:
        plan = await homework_volume_service.compute(
            session, student_id=sid, now=now,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "students-dashboard: не посчитался on_track для %s", sid,
            exc_info=True,
        )
        return sid, None
    if plan.program_kind is None or plan.program_deadline is None:
        return sid, None
    return sid, {
        "kind": plan.program_kind,
        "deadline": plan.program_deadline.isoformat(),
        "on_track": _program_on_track(plan),
    }


async def _refresh_program_pace(
    bind: AsyncEngine, sids: list[int], day: str,
) -> None:
    """Фоновый пересчёт протухших вердиктов (прод, bind — движок).

    Каждому ученику — своя сессия: общая AsyncSession к одному соединению
    конкурентно не работает (поэтому gather по общей сессии не лечил 504).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=bind, expire_on_commit=False)
    semaphore = asyncio.Semaphore(8)
    now = datetime.now(MSK)
    try:
        async def _one(sid: int) -> tuple[int, Optional[dict[str, Any]]]:
            async with semaphore:
                async with factory() as session:
                    return await _compute_program_pace(session, sid, now)

        results = await asyncio.gather(*[_one(sid) for sid in sids])
        ts = time_module.monotonic()
        for sid, payload in results:
            _program_pace_cache[(sid, day)] = (ts, payload)
    except asyncio.CancelledError:  # noqa: BLE001
        raise
    except Exception:  # noqa: BLE001
        logger.warning(
            "students-dashboard: фоновый пересчёт on_track не удался",
            exc_info=True,
        )
    finally:
        for sid in sids:
            _program_pace_refreshing.discard((sid, day))
        _program_pace_refresh_tasks.discard(asyncio.current_task())  # type: ignore[arg-type]


async def _program_pace_by_student(
    db: AsyncSession, student_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """Блок «успевает ли к экзамену» для учеников на программных курсах.

    Расчёт — тот же, что в дашборде ученика (tsk-815): план объёма
    `homework_volume_service.compute`, а не параллельная формула. `None`
    целиком — ученик не записан ни на одну программу подготовки.
    """
    from app.services import homework_volume_service

    oge_ids, ege_ids = homework_volume_service.program_course_ids()
    program_ids = set(oge_ids) | set(ege_ids)
    if not student_ids or not program_ids:
        return {}

    rows = (
        await db.execute(
            _PROGRAM_ENROLLMENT_SQL, {"ids": list(student_ids)},
        )
    ).fetchall()
    on_program = {
        int(r.student_id)
        for r in rows
        if int(r.course_id) in program_ids
    }
    if not on_program:
        return {}

    now = datetime.now(MSK)
    day = now.date().isoformat()

    # Кэш: свежие записи — из памяти; протухшие — отдаём как есть и в фоне
    # пересчитываем (прод); холодные (записи нет) — считаем inline. В тестах
    # bind — Connection одной откатываемой транзакции (conftest tsk-333):
    # параллельные сессии и фоновые задачи там невозможны, протухшие
    # пересчитываем inline поверх той же сессии `db`.
    now_ts = time_module.monotonic()
    verdicts: dict[int, Optional[dict[str, Any]]] = {}
    cold: set[int] = set()
    stale: set[int] = set()
    for sid in on_program:
        cached = _program_pace_cache.get((sid, day))
        if cached is None:
            cold.add(sid)
        elif cached[0] >= now_ts - _PROGRAM_PACE_CACHE_TTL_SEC:
            verdicts[sid] = cached[1]
        else:
            stale.add(sid)

    bind = db.get_bind()
    engine_bind = isinstance(bind, AsyncEngine)
    inline = cold | (set() if engine_bind else stale)

    if inline:
        if engine_bind:
            from sqlalchemy.ext.asyncio import async_sessionmaker

            factory = async_sessionmaker(bind=bind, expire_on_commit=False)
            semaphore = asyncio.Semaphore(8)

            async def _one_session(
                sid: int,
            ) -> tuple[int, Optional[dict[str, Any]]]:
                async with semaphore:
                    async with factory() as session:
                        return await _compute_program_pace(
                            session, sid, now,
                        )

            results = await asyncio.gather(
                *[_one_session(sid) for sid in inline],
            )
        else:
            results = [
                await _compute_program_pace(db, sid, now)
                for sid in sorted(inline)
            ]
        ts = time_module.monotonic()
        for sid, payload in results:
            _program_pace_cache[(sid, day)] = (ts, payload)
            verdicts[sid] = payload

    if engine_bind and stale:
        to_refresh = [
            sid for sid in sorted(stale)
            if (sid, day) not in _program_pace_refreshing
        ]
        for sid in stale:
            # Протухшее значение всё ещё лучше пустой колонки: вердикт
            # меняется за дни, а расхождение — не больше TTL.
            verdicts[sid] = _program_pace_cache[(sid, day)][1]
        if to_refresh:
            for sid in to_refresh:
                _program_pace_refreshing.add((sid, day))
            task = asyncio.create_task(
                _refresh_program_pace(bind, to_refresh, day),
            )
            _program_pace_refresh_tasks.add(task)

    return {sid: v for sid, v in verdicts.items() if v is not None}


async def get_students_dashboard(
    db: AsyncSession, *, from_date: str, to_date: str,
    student_id: Optional[int] = None,
    exclude_graduates_test: bool = True,
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

    # tsk-1535: активные подписи на «Выпускник»/«Test» исключаются по
    # умолчанию; с exclude_graduates_test=False дашборд показывает всех.
    if exclude_graduates_test and student_ids:
        rows = (
            await db.execute(
                _EXCLUDED_PLANS_SQL,
                {"plan_ids": list(_EXCLUDED_PLAN_IDS), "ids": list(student_ids)},
            )
        ).fetchall()
        student_ids -= {int(r.student_id) for r in rows}

    last_activity = (
        await _period_rows(db, _LAST_ACTIVITY_SQL, {"ids": list(student_ids)})
        if student_ids
        else {}
    )

    names: dict[int, Optional[str]] = {}
    if student_ids:
        rows = (
            await db.execute(
                text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
                {"ids": list(student_ids)},
            )
        ).fetchall()
        names = {int(r.id): r.full_name for r in rows}

    # tsk-1536: вердикт «успевает/не успевает к экзамену» для учеников
    # программ подготовки (ЕГЭ/ОГЭ). Прочим ученикам поле остаётся None.
    program_pace = await _program_pace_by_student(db, student_ids)

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
            # tsk-1536: None — ученик не на программе подготовки с дедлайном.
            "program": program_pace.get(sid),
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
