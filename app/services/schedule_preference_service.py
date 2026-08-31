"""Пожелания ученика по расписанию: чтение, сохранение, история, охват (tsk-674).

Фаза 1 осеннего расписания. Собирает то, что ученик просит, — решение о сетке
принимает методист позже (фаза 2), здесь его нет.

Что важно помнить читающему:

- Время в базе МОСКОВСКОЕ, всегда. Сетку школа ведёт по Москве, а показать
  ученику его собственный час — работа клиента (tsk-588). Хранить «как у
  ученика» нельзя: пояс в профиле меняется, и тогда пожелание молча уехало бы
  вместе с ним.
- Каждое сохранение пишет снимок в `student_schedule_preference_revision`.
  Пожелания правятся весь срок обучения, и методисту нужно видеть не только
  «что сейчас», но и «что было в августе».
- Аудитория опроса — учащиеся, кроме выпускников (`alumni`) и демо (`demo`).
  Это дословное решение оператора от 2026-08-25.
- **Кому показываем и кого считаем — это два разных списка** (tsk-712).
  Тестовым учёткам (`test`) опрос показывается и напоминание им приходит —
  иначе кабинет не на чем проверять, — но в охват и в спрос по часам они не
  идут ни числителем, ни знаменателем.
"""
from __future__ import annotations

import json
import logging
from datetime import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.schedule_preference import (
    DEFAULT_LESSONS_PER_WEEK,
    SCHEDULE_GRID,
    SchedulePreferenceHour,
    SchedulePreferenceWrite,
)

logger = logging.getLogger(__name__)

#: Тарифы, которым опрос не показывается: выпускник уже отучился, демо — ещё
#: не ученик школы в смысле расписания.
EXCLUDED_PLAN_CODES = ("alumni", "demo")

#: Тарифы, которым опрос показывается, но которых НЕ считают (tsk-712).
#: Тестовые учётки заведены, чтобы проверять на них кабинет: плашку и
#: напоминание они получают наравне со всеми — иначе проверять нечего. А вот
#: в «сколько заполнили / кто молчит», в спрос по часам и в раскладку слотов
#: им нельзя: методист по этим числам собирает расписание живым людям, и
#: каждая пятая строка была бы выдуманной.
NOT_COUNTED_PLAN_CODES = ("test",)


def _sql_codes(codes: tuple[str, ...]) -> str:
    """Список кодов тарифов для `IN (...)`. Значения свои, не пользовательские."""
    return ", ".join(f"'{code}'" for code in codes)


def _plan_filter(codes: tuple[str, ...]) -> str:
    """SQL-условие «тариф человека не из этого списка».

    Человек без действующей подписки (`cur.code IS NULL`) подходит всегда:
    отсутствие тарифа — не повод выкинуть его из опроса.
    """
    return f"(cur.code IS NULL OR cur.code NOT IN ({_sql_codes(codes)}))"


_AUDIENCE_CORE = """
    FROM users u
    JOIN user_roles ur ON ur.user_id = u.id
    JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
    LEFT JOIN (
        SELECT ss.student_id, sp.code
          FROM student_subscription ss
          JOIN subscription_plan sp ON sp.id = ss.plan_id
         WHERE ss.ends_on IS NULL
    ) cur ON cur.student_id = u.id
   WHERE u.is_active
     AND {plan_filter}
"""

#: Кусок SQL: **кому опрос показывается**. По нему живут флаг в `/me` и
#: напоминания. Держится одной строкой, потому что показ и напоминание обязаны
#: совпадать: плашка без напоминания и напоминание без плашки одинаково
#: выглядят как поломка.
AUDIENCE_FROM = _AUDIENCE_CORE.format(plan_filter=_plan_filter(EXCLUDED_PLAN_CODES))

#: Кусок SQL: **кого считают**. Та же аудитория минус тестовые. По нему живут
#: сводка охвата, спрос по часам и вёрстка расписания (tsk-674 фаза 2). Собран
#: из того же куска, что и показ, — иначе два списка разъедутся молча, и
#: методист увидит «в опросе 61, а в вёрстке 49», не понимая, кто прав.
COUNTED_AUDIENCE_FROM = _AUDIENCE_CORE.format(
    plan_filter=_plan_filter(EXCLUDED_PLAN_CODES + NOT_COUNTED_PLAN_CODES)
)


class SchedulePreferenceError(ValueError):
    """Пожелание нельзя принять: сообщение уже написано для человека."""


async def _open_hours(db: AsyncSession, student_id: int) -> set[tuple[int, time]] | None:
    """Часы, где ученику есть куда встать; `None` — расписания ещё нет вовсе.

    Импорт локальный: `schedule_booking_service` сам зовёт этот модуль, и на
    верхнем уровне вышел бы круг.
    """
    from app.services import schedule_booking_service

    hours = await schedule_booking_service.open_hours(db, student_id)
    # Слотов нет совсем — значит расписание ещё не составлено, и опрос работает
    # по всей сетке, как и задумывался (фаза 1).
    return hours or None


def grid_as_days(open_hours: set[tuple[int, time]] | None = None) -> list[dict[str, Any]]:
    """Сетка для клиента: список дней с допустимыми часами начала.

    tsk-746: рядом с каждым днём едут `open_hours` — часы, где занятие
    действительно есть. Пустой набор (сетку спрашивают до вёрстки) означает
    «открыты все»: иначе опрос перед составлением расписания стал бы невозможен.
    """
    return [
        {
            "weekday": weekday,
            "hours": [time(hour=h) for h in hours],
            "open_hours": [
                time(hour=h)
                for h in hours
                if open_hours is None or (weekday, time(hour=h)) in open_hours
            ],
        }
        for weekday, hours in sorted(SCHEDULE_GRID.items())
    ]


def validate(
    body: SchedulePreferenceWrite,
    open_hours: set[tuple[int, time]] | None = None,
) -> list[SchedulePreferenceHour]:
    """Проверить пожелание целиком и вернуть нормализованный список часов.

    Три правила, и все три — про то, можно ли по этому пожеланию собрать
    человеку расписание:

    1. Час попадает в сетку (Пн-Чт 12-19, Сб 9-14 МСК). Иначе ученик выбрал бы
       время, которого осенью не существует, — ровно та ошибка, ради которой
       опрос и затевался.
    2. Час не назван дважды: «желательный» и «возможный» одновременно —
       противоречие, а не уточнение.
    3. Желательных часов не меньше, чем занятий в неделю. Требование оператора:
       иначе вёрстка упирается в человека, которому некуда встать.
    4. tsk-746: час выбран из тех, где занятие ЕСТЬ (`open_hours`). Пока
       расписание не составлено, набор не передаётся и правило не работает —
       именно так собирался осенний опрос. После вёрстки оно обязательно:
       выбранный «пустой» час не даёт человеку занятия вовсе.
    """
    seen: dict[tuple[int, time], str] = {}
    for hour in body.hours:
        allowed = SCHEDULE_GRID.get(hour.weekday)
        if allowed is None or hour.start_time.hour not in allowed:
            raise SchedulePreferenceError(
                "Этот час вне расписания школы: занятия идут с понедельника по "
                "четверг с 12:00 до 19:00 и в субботу с 09:00 до 14:00 по Москве."
            )
        key = (hour.weekday, hour.start_time)
        if open_hours is not None and key not in open_hours:
            raise SchedulePreferenceError(
                "В это время занятий нет — выберите час, в котором уже идёт "
                "группа. Если ничего не подходит, нажмите «Не нашёл подходящее "
                "время»: методист подберёт вариант."
            )
        if key in seen:
            raise SchedulePreferenceError(
                "Один и тот же час выбран дважды — он может быть либо желательным, "
                "либо возможным."
            )
        seen[key] = hour.kind

    preferred = [h for h in body.hours if h.kind == "preferred"]
    if len(preferred) < body.lessons_per_week:
        raise SchedulePreferenceError(
            f"Желательных часов нужно хотя бы столько, сколько занятий в неделю — "
            f"{body.lessons_per_week}. Сейчас выбрано {len(preferred)}."
        )

    return sorted(body.hours, key=lambda h: (h.weekday, h.start_time))


async def is_audience(db: AsyncSession, user_id: int) -> bool:
    """Показывать ли этому человеку опрос."""
    row = (
        await db.execute(
            text(f"SELECT 1 {AUDIENCE_FROM} AND u.id = :uid LIMIT 1"),
            {"uid": user_id},
        )
    ).first()
    return row is not None


async def is_pending(db: AsyncSession, user_id: int) -> bool:
    """Нужно ли напомнить: человек в аудитории и пожеланий ещё не оставил.

    Это ровно то, что уходит в `GET /me` и включает напоминание на всех
    экранах кабинета. Один запрос: флаг читается на каждой загрузке профиля.
    """
    row = (
        await db.execute(
            text(
                f"""
                SELECT (
                    SELECT 1 FROM student_schedule_preference p
                     WHERE p.student_id = u.id
                ) IS NULL AS pending
                {AUDIENCE_FROM} AND u.id = :uid
                LIMIT 1
                """
            ),
            {"uid": user_id},
        )
    ).first()
    return bool(row[0]) if row is not None else False


async def schedule_ends_on(db: AsyncSession, student_id: int):
    """Дата, когда у ученика заканчивается НЫНЕШНЕЕ расписание, или `None`.

    Считается только когда у человека есть слоты и **все** они с датой
    окончания (tsk-679): если хоть один бессрочный, расписание не кончается, и
    говорить ученику «занятия заканчиваются» было бы неправдой.

    Нужна кабинету ученика: после смены сетки его календарь пустеет, и пустой
    список без объяснения читается как поломка. Дата берётся с сервера, а не
    зашивается в экран, — иначе следующая смена расписания потребует релиза
    клиента, а до тех пор экран уверенно врал бы прошлогодней датой.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT count(*)                              AS total,
                       count(*) FILTER (WHERE ls.active_until IS NULL) AS endless,
                       max(ls.active_until)                  AS ends_on
                  FROM lesson_slot_student lss
                  JOIN lesson_slot ls ON ls.id = lss.slot_id
                 WHERE lss.student_id = :sid AND lss.is_active AND ls.is_active
                """
            ),
            {"sid": student_id},
        )
    ).first()
    if row is None or int(row[0]) == 0 or int(row[1]) > 0:
        return None
    return row[2]


async def get_preference(db: AsyncSession, student_id: int) -> dict[str, Any]:
    """Действующее пожелание ученика (или умолчания, если он его не оставлял)."""
    head = (
        await db.execute(
            text(
                "SELECT id, lessons_per_week, comment, updated_at "
                "  FROM student_schedule_preference WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).first()

    hours: list[SchedulePreferenceHour] = []
    if head is not None:
        rows = (
            await db.execute(
                text(
                    "SELECT weekday, start_time, kind "
                    "  FROM student_schedule_preference_hour "
                    " WHERE preference_id = :pid ORDER BY weekday, start_time"
                ),
                {"pid": head[0]},
            )
        ).fetchall()
        hours = [
            SchedulePreferenceHour(weekday=r[0], start_time=r[1], kind=r[2]) for r in rows
        ]

    return {
        "student_id": student_id,
        "is_filled": head is not None,
        "lessons_per_week": head[1] if head is not None else DEFAULT_LESSONS_PER_WEEK,
        "hours": hours,
        "comment": head[2] if head is not None else None,
        "updated_at": head[3] if head is not None else None,
        "is_audience": await is_audience(db, student_id),
        "grid": grid_as_days(await _open_hours(db, student_id)),
        # tsk-679: когда заканчивается нынешнее расписание. Кабинет ученика по
        # этой дате объясняет пустой календарь вместо того, чтобы молчать.
        "schedule_ends_on": await schedule_ends_on(db, student_id),
    }


async def save_preference(
    db: AsyncSession,
    student_id: int,
    body: SchedulePreferenceWrite,
    *,
    changed_by: Optional[int] = None,
) -> dict[str, Any]:
    """Сохранить пожелание: перезаписать действующее и добавить снимок в историю.

    Часы переписываются целиком (`DELETE` + `INSERT`), а не сверяются построчно:
    их единицы, а частичная синхронизация — источник расхождений, которые
    видно только на вёрстке, то есть слишком поздно.
    """
    # tsk-746: принимаем только часы, где занятие есть. Проверка на сервере, а
    # не только на экране: пожелание уходит обычным PUT, и «серая» кнопка на
    # клиенте от повторной отправки не спасает.
    hours = validate(body, await _open_hours(db, student_id))

    pref_id = (
        await db.execute(
            text(
                """
                INSERT INTO student_schedule_preference
                       (student_id, lessons_per_week, comment, updated_by, updated_at)
                VALUES (:sid, :lpw, :comment, :by, now())
                ON CONFLICT (student_id) DO UPDATE
                   SET lessons_per_week = EXCLUDED.lessons_per_week,
                       comment          = EXCLUDED.comment,
                       updated_by       = EXCLUDED.updated_by,
                       updated_at       = now()
                RETURNING id
                """
            ),
            {
                "sid": student_id,
                "lpw": body.lessons_per_week,
                "comment": body.comment,
                "by": changed_by,
            },
        )
    ).scalar_one()

    await db.execute(
        text("DELETE FROM student_schedule_preference_hour WHERE preference_id = :pid"),
        {"pid": pref_id},
    )
    for hour in hours:
        await db.execute(
            text(
                "INSERT INTO student_schedule_preference_hour "
                "       (preference_id, weekday, start_time, kind) "
                "VALUES (:pid, :wd, :st, :kind)"
            ),
            {
                "pid": pref_id,
                "wd": hour.weekday,
                "st": hour.start_time,
                "kind": hour.kind,
            },
        )

    snapshot = [
        {
            "weekday": h.weekday,
            "start_time": h.start_time.strftime("%H:%M"),
            "kind": h.kind,
        }
        for h in hours
    ]
    await db.execute(
        text(
            "INSERT INTO student_schedule_preference_revision "
            "       (student_id, lessons_per_week, hours, comment, source, changed_by) "
            "VALUES (:sid, :lpw, CAST(:hours AS jsonb), :comment, :source, :by)"
        ),
        {
            "sid": student_id,
            "lpw": body.lessons_per_week,
            "hours": json.dumps(snapshot, ensure_ascii=False),
            "comment": body.comment,
            "source": body.source,
            "by": changed_by,
        },
    )
    await db.commit()

    logger.info(
        "tsk-674: ученик %s сохранил пожелания — %s занятий в неделю, "
        "желательных часов %s, возможных %s (источник %s)",
        student_id,
        body.lessons_per_week,
        sum(1 for h in hours if h.kind == "preferred"),
        sum(1 for h in hours if h.kind == "possible"),
        body.source,
    )
    return await get_preference(db, student_id)


async def clear_preference(
    db: AsyncSession, student_id: int, *, changed_by: Optional[int] = None
) -> dict[str, Any]:
    """Снять анкету ученика: он снова считается не ответившим (tsk-714).

    Зачем это отдельное действие. Анкету можно переписать, но нельзя сохранить
    пустой: проверка требует желательных часов не меньше, чем занятий в неделю.
    А отменить ответ иногда нужно — ученик нажал за брата, сотрудник заполнил
    под своей учётной записью, человек просит «забудьте, что я выбирал». Пока
    строка есть, он числится ответившим, его часы идут в спрос и в раскладку
    слотов, то есть влияют на расписание живых людей.

    **История остаётся.** Снятие пишет в `..._revision` свой снимок с пустыми
    часами и `source='staff'`: методисту потом важно видеть и то, что человек
    просил в августе, и то, что анкету сняли. Стереть историю значило бы
    отменить сам смысл, ради которого её завели.
    """
    head = (
        await db.execute(
            text(
                "SELECT id, lessons_per_week FROM student_schedule_preference "
                " WHERE student_id = :sid"
            ),
            {"sid": student_id},
        )
    ).first()
    if head is None:
        raise SchedulePreferenceError(
            "У этого ученика анкеты нет — снимать нечего."
        )

    # Часы уходят каскадом вместе со строкой анкеты (FK `ON DELETE CASCADE`),
    # отдельного удаления не нужно.
    await db.execute(
        text("DELETE FROM student_schedule_preference WHERE id = :pid"),
        {"pid": head[0]},
    )
    await db.execute(
        text(
            "INSERT INTO student_schedule_preference_revision "
            "       (student_id, lessons_per_week, hours, comment, source, changed_by) "
            "VALUES (:sid, :lpw, '[]'::jsonb, :comment, 'staff', :by)"
        ),
        {
            "sid": student_id,
            "lpw": head[1],
            "comment": "Анкета снята сотрудником",
            "by": changed_by,
        },
    )
    await db.commit()

    logger.info(
        "tsk-714: анкета ученика %s снята сотрудником %s — он снова в молчащих",
        student_id, changed_by,
    )
    return await get_preference(db, student_id)


async def list_history(
    db: AsyncSession, student_id: int, *, limit: int = 50
) -> list[dict[str, Any]]:
    """История правок пожеланий, свежие сверху."""
    rows = (
        await db.execute(
            text(
                "SELECT id, lessons_per_week, hours, comment, source, changed_by, created_at "
                "  FROM student_schedule_preference_revision "
                " WHERE student_id = :sid ORDER BY created_at DESC, id DESC LIMIT :lim"
            ),
            {"sid": student_id, "lim": limit},
        )
    ).fetchall()

    history: list[dict[str, Any]] = []
    for r in rows:
        raw_hours = r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]")
        history.append(
            {
                "id": r[0],
                "lessons_per_week": r[1],
                "hours": [
                    SchedulePreferenceHour(
                        weekday=h["weekday"],
                        start_time=time.fromisoformat(h["start_time"]),
                        kind=h["kind"],
                    )
                    for h in raw_hours
                ],
                "comment": r[3],
                "source": r[4],
                "changed_by": r[5],
                "created_at": r[6],
            }
        )
    return history


async def get_summary(db: AsyncSession) -> dict[str, Any]:
    """Охват опроса: сколько заполнили, кто молчит, какой час сколько просят.

    Без этой сводки 30 августа выяснилось бы, что данных нет, а времени уже
    нет, — прямая формулировка оператора, ради неё сводка и делается.

    Молчащие идут первыми: методист открывает экран, чтобы понять, кого
    дёргать, а не чтобы полюбоваться заполнившими.

    Считаются здесь только настоящие ученики (tsk-712): тестовые учётки опрос
    видят и заполнить его могут, но в числа не входят — ни в список, ни в
    спрос по часам. Сколько их отброшено, видно отдельным числом: без него
    падение «61 → 51» на экране читалось бы как пропавшие люди.
    """
    rows = (
        await db.execute(
            text(
                f"""
                SELECT u.id,
                       u.full_name,
                       u.email,
                       u.timezone,
                       cur.code AS plan_code,
                       (pref.id IS NOT NULL) AS is_filled,
                       pref.lessons_per_week,
                       pref.updated_at,
                       COALESCE(cnt.preferred_count, 0) AS preferred_count,
                       COALESCE(cnt.possible_count, 0) AS possible_count,
                       COALESCE(slots.labels, ARRAY[]::text[]) AS current_slots
                  FROM users u
                  JOIN user_roles ur ON ur.user_id = u.id
                  JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
                  LEFT JOIN (
                      SELECT ss.student_id, sp.code
                        FROM student_subscription ss
                        JOIN subscription_plan sp ON sp.id = ss.plan_id
                       WHERE ss.ends_on IS NULL
                  ) cur ON cur.student_id = u.id
                  LEFT JOIN student_schedule_preference pref ON pref.student_id = u.id
                  LEFT JOIN LATERAL (
                      SELECT
                        COUNT(*) FILTER (WHERE h.kind = 'preferred') AS preferred_count,
                        COUNT(*) FILTER (WHERE h.kind = 'possible')  AS possible_count
                        FROM student_schedule_preference_hour h
                       WHERE h.preference_id = pref.id
                  ) cnt ON TRUE
                  LEFT JOIN LATERAL (
                      SELECT array_agg(
                               to_char(ls.start_time, 'HH24:MI') || ' ' ||
                               CASE ls.weekday
                                 WHEN 0 THEN 'пн' WHEN 1 THEN 'вт' WHEN 2 THEN 'ср'
                                 WHEN 3 THEN 'чт' WHEN 4 THEN 'пт' WHEN 5 THEN 'сб'
                                 ELSE 'вс' END
                               ORDER BY ls.weekday, ls.start_time
                             ) AS labels
                        FROM lesson_slot_student lss
                        JOIN lesson_slot ls ON ls.id = lss.slot_id
                       WHERE lss.student_id = u.id AND lss.is_active AND ls.is_active
                  ) slots ON TRUE
                 WHERE u.is_active
                   AND {_plan_filter(EXCLUDED_PLAN_CODES + NOT_COUNTED_PLAN_CODES)}
                 ORDER BY (pref.id IS NOT NULL), u.full_name NULLS LAST, u.id
                """
            )
        )
    ).fetchall()

    students = [
        {
            "student_id": r[0],
            "full_name": r[1],
            "email": r[2],
            "timezone": r[3],
            "plan_code": r[4],
            "is_filled": bool(r[5]),
            "lessons_per_week": r[6],
            "updated_at": r[7],
            "preferred_count": int(r[8]),
            "possible_count": int(r[9]),
            "current_slots": list(r[10] or []),
        }
        for r in rows
    ]

    # Спрос считается по той же счётной аудитории, что и список (tsk-712).
    # Раньше здесь фильтра не было вовсе: анкета тестовой учётки добавляла
    # заявок на час, и раскладка могла отвести под этот час живой слот.
    demand_rows = (
        await db.execute(
            text(
                f"""
                WITH counted AS (
                    SELECT u.id AS student_id
                    {COUNTED_AUDIENCE_FROM}
                )
                SELECT h.weekday,
                       h.start_time,
                       COUNT(*) FILTER (WHERE h.kind = 'preferred') AS preferred_count,
                       COUNT(*) FILTER (WHERE h.kind = 'possible')  AS possible_count
                  FROM student_schedule_preference_hour h
                  JOIN student_schedule_preference p ON p.id = h.preference_id
                  JOIN counted c ON c.student_id = p.student_id
                 GROUP BY h.weekday, h.start_time
                 ORDER BY h.weekday, h.start_time
                """
            )
        )
    ).fetchall()

    # Сколько учёток показ видит, а счёт не берёт. Число показывается рядом со
    # сводкой: иначе разница с прошлым охватом выглядит как потеря людей.
    not_counted_total = int(
        (
            await db.execute(
                text(
                    f"SELECT count(*) {AUDIENCE_FROM} "
                    f"AND cur.code IN ({_sql_codes(NOT_COUNTED_PLAN_CODES)})"
                )
            )
        ).scalar_one()
    )

    filled = [s for s in students if s["is_filled"]]
    return {
        "audience_total": len(students),
        "not_counted_total": not_counted_total,
        "filled_total": len(filled),
        "silent_total": len(students) - len(filled),
        "lessons_demand": sum(s["lessons_per_week"] or 0 for s in filled),
        "students": students,
        "demand": [
            {
                "weekday": r[0],
                "start_time": r[1],
                "preferred_count": int(r[2]),
                "possible_count": int(r[3]),
            }
            for r in demand_rows
        ],
        "grid": grid_as_days(),
    }
