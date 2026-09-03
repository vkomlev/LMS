"""Запись ученика в свободные слоты и заявка методисту (tsk-674, фаза 3).

Что здесь происходит и почему именно так.

**Показываем только то, куда можно встать.** Слот попадает в ответ, если он
активен, стоит в осенней сетке (Пн-Чт 12:00-19:00, Сб 09:00-14:00 МСК), доживёт
хотя бы до ближайшего своего занятия и в нём меньше десяти человек. Десятый —
потолок оператора: слот, где уже десять, не показывается вовсе, а не
показывается серым. Свободным (`free`) считается слот, где людей меньше цели
5-6; остальные — частично свободные (`partial`).

**Проверяем ещё раз в момент записи.** Между загрузкой экрана и нажатием
кнопки место мог занять другой человек, поэтому `join_slot` не верит клиенту:
он перечитывает слот под блокировкой строки и отказывает словами, которые
можно показать ученику.

**Ученик не может записаться больше, чем сам просил.** Указал два занятия в
неделю — встанет максимум в два слота (решение оператора 2026-08-27). Причина
не в педантичности: за каждое занятие идёт начисление, и случайный лишний клик
превращается в счёт. Захотел больше — сначала правит пожелания, это одна
кнопка на том же экране.

**Не нашлось времени — заявка методисту.** Она живёт в своей таблице со
статусом, а не только уведомлением: сигнал в этой школе уже дважды тонул
непрочитанным (tsk-591, tsk-652). Уведомление тоже уходит — и ведёт в очередь.

Время везде московское. Пояс ученика дорисовывает клиент (tsk-588).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.schedule_booking import (
    BOOKING_MAX,
    BookableSlot,
    ScheduleSlotRequestRead,
    availability_for,
    is_bookable_count,
)
from app.schemas.schedule_preference import (
    GRID_TIMEZONE,
    SchedulePreferenceHour,
)
from app.services import audit_service, inbox_service, lesson_calendar_service
from app.services import schedule_preference_service
from app.services.schedule_plan_service import in_grid
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

#: Вид уведомления методисту. Тот же список kind'ов читает и кабинет
#: (`GET /methodist/escalations/pending`), и бот методиста в TG_LMS.
REQUEST_KIND = "schedule_slot_request"

#: Дни недели словами — для текста уведомления методисту.
WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

#: Смещение ключа advisory-блокировки записи, чтобы он не пересекался с чужими
#: (в проекте уже есть `pg_try_advisory_xact_lock` у эскалаций проверок).
#: К нему прибавляется `student_id`: сериализуются только нажатия одного и того
#: же человека, разные ученики друг друга не ждут.
_BOOKING_LOCK_NAMESPACE = 0x74736B36_74000000  # ascii-подобное "tsk674"


def _today_moscow() -> date:
    """Сегодняшняя дата по Москве: сетка ведётся в московском времени."""
    return datetime.now(ZoneInfo(GRID_TIMEZONE)).date()


def next_occurrence_date(weekday: int, today: Optional[date] = None) -> date:
    """Дата ближайшего занятия в этот день недели, считая с сегодня."""
    base = today or _today_moscow()
    shift = (weekday - base.weekday()) % 7
    return base + timedelta(days=shift)


def slot_is_alive(
    weekday: int, active_until: Optional[date], today: Optional[date] = None
) -> bool:
    """Доживёт ли слот хотя бы до ближайшего своего занятия.

    Старая летняя сетка помечена датой окончания 30 августа (tsk-679). Такой
    слот формально ещё активен, но записывать в него нового человека значит
    пообещать занятие, которого не будет. Бессрочный слот (`active_until IS
    NULL`) живёт всегда.
    """
    if active_until is None:
        return True
    return active_until >= next_occurrence_date(weekday, today)


def _hour_label(weekday: int, start_time: time) -> str:
    day = WEEKDAY_SHORT[weekday] if 0 <= weekday < len(WEEKDAY_SHORT) else str(weekday)
    return f"{day} {start_time:%H:%M}"


async def open_hours(
    db: AsyncSession, student_id: int
) -> tuple[set[tuple[int, time]], set[tuple[int, time]]]:
    """Часы, в которых ученику есть куда встать, — и отдельно часы, где группа
    ЕСТЬ, но набрана под потолок (tsk-746, tsk-786).

    Расписание уже составлено, и опрос «когда вам удобно» перестал быть опросом:
    выбирая час, где слота нет, человек не получает занятия вовсе. Так и вышло
    31.08 у новичка — он отметил четверг 17:00, которого в сетке нет, и остался
    с одним занятием вместо двух.

    Свои часы included в открытых всегда: человек уже занимается в этом слоте,
    и запретить ему назвать своё же время было бы странно, даже если группа
    полна.

    Второе множество (`full`) — не для проверки «можно ли выбрать» (для неё
    по-прежнему хватает первого), а для экрана: пожелания ученик читал одинаково
    пустыми что в час без единой группы, что в час с набранной, — и щёлкал оба
    вида одинаково (tsk-786, живая заявка 03.09).
    """
    open_h: set[tuple[int, time]] = set()
    full_h: set[tuple[int, time]] = set()
    today = _today_moscow()
    for row in await _load_slots(db, student_id):
        key = (row["weekday"], row["start_time"])
        if row["is_mine"]:
            open_h.add(key)
            continue
        if not slot_is_alive(row["weekday"], row["active_until"], today):
            continue
        if not in_grid(key):
            continue
        if is_bookable_count(row["student_count"]):
            open_h.add(key)
        else:
            full_h.add(key)
    # Один час может держать группы разных преподавателей: если хотя бы одна
    # из них открыта, час открыт для ученика целиком (он выбирает час, не
    # преподавателя) — даже если другая группа в тот же час уже набрана.
    # Без вычитания `full_h` получил бы тот же ключ вторым проходом цикла, и
    # экран показал бы один час одновременно «набрана» и доступным для клика.
    full_h -= open_h
    return open_h, full_h


async def _load_slots(db: AsyncSession, student_id: int) -> list[dict[str, Any]]:
    """Все активные слоты с числом участников и пометкой «ученик уже здесь»."""
    rows = (
        await db.execute(
            text(
                """
                SELECT ls.id,
                       ls.teacher_id,
                       t.full_name,
                       ls.weekday,
                       ls.start_time,
                       ls.duration_minutes,
                       ls.active_until,
                       COUNT(lss.id) FILTER (WHERE lss.is_active) AS student_count,
                       COUNT(lss.id) FILTER (
                           WHERE lss.is_active AND lss.student_id = :sid
                       ) > 0 AS is_mine
                  FROM lesson_slot ls
                  LEFT JOIN users t ON t.id = ls.teacher_id
                  LEFT JOIN lesson_slot_student lss ON lss.slot_id = ls.id
                 WHERE ls.is_active
                 GROUP BY ls.id, ls.teacher_id, t.full_name, ls.weekday,
                          ls.start_time, ls.duration_minutes, ls.active_until
                 ORDER BY ls.weekday, ls.start_time, ls.id
                """
            ),
            {"sid": student_id},
        )
    ).fetchall()

    return [
        {
            "slot_id": int(r[0]),
            "teacher_id": int(r[1]),
            "teacher_name": r[2],
            "weekday": int(r[3]),
            "start_time": r[4],
            "duration_minutes": int(r[5]),
            "active_until": r[6],
            "student_count": int(r[7] or 0),
            "is_mine": bool(r[8]),
        }
        for r in rows
    ]


def _match_for(
    weekday: int, start_time: time, preferred: set, possible: set
) -> str:
    key = (weekday, start_time)
    if key in preferred:
        return "preferred"
    if key in possible:
        return "possible"
    return "none"


def _to_slot(row: dict[str, Any], match: str) -> BookableSlot:
    count = row["student_count"]
    return BookableSlot(
        slot_id=row["slot_id"],
        weekday=row["weekday"],
        start_time=row["start_time"],
        duration_minutes=row["duration_minutes"],
        teacher_id=row["teacher_id"],
        teacher_name=row["teacher_name"],
        student_count=count,
        # От потолка ЗАПИСИ (tsk-746): это «сколько мест ещё открыто ученику»,
        # а не «сколько человек методист может свести руками».
        seats_left=max(0, BOOKING_MAX - count),
        availability=availability_for(count),
        match=match,  # type: ignore[arg-type]
        active_until=row["active_until"],
        is_mine=row["is_mine"],
    )


async def get_bookable(db: AsyncSession, student_id: int) -> dict[str, Any]:
    """Что ученик видит на экране выбора времени.

    Порядок вариантов — не косметика: сперва часы, которые человек сам назвал
    желательными, потом возможные, и только потом остальные. Иначе выбор из
    двадцати одинаковых кнопок делает бессмысленным опрос, который он уже
    заполнил.
    """
    pref = await schedule_preference_service.get_preference(db, student_id)
    preferred = {
        (h.weekday, h.start_time) for h in pref["hours"] if h.kind == "preferred"
    }
    possible = {
        (h.weekday, h.start_time) for h in pref["hours"] if h.kind == "possible"
    }

    today = _today_moscow()
    options: list[BookableSlot] = []
    mine: list[BookableSlot] = []
    for row in await _load_slots(db, student_id):
        alive = slot_is_alive(row["weekday"], row["active_until"], today)
        match = _match_for(row["weekday"], row["start_time"], preferred, possible)
        if row["is_mine"]:
            # Свои занятия показываем целиком, включая те, что доживают старую
            # сетку: человек должен видеть, куда он ходит сейчас.
            mine.append(_to_slot(row, match))
            continue
        if not alive or not in_grid((row["weekday"], row["start_time"])):
            continue
        if not is_bookable_count(row["student_count"]):
            # В слоте больше восьми — не показываем вовсе (tsk-746, запрет оператора).
            continue
        options.append(_to_slot(row, match))

    match_order = {"preferred": 0, "possible": 1, "none": 2}
    options.sort(
        key=lambda s: (
            match_order[s.match],
            {"free": 0, "partial": 1, "crowded": 2}[s.availability],
            s.weekday,
            s.start_time,
        )
    )

    booked_count = sum(
        1 for s in mine if slot_is_alive(s.weekday, s.active_until, today)
    )
    open_request = await get_open_request(db, student_id)

    return {
        "student_id": student_id,
        "is_audience": pref["is_audience"],
        "preference_filled": pref["is_filled"],
        "lessons_per_week": pref["lessons_per_week"],
        "booked_count": booked_count,
        "can_book_more": booked_count < pref["lessons_per_week"],
        "slots": options,
        "my_slots": mine,
        "grid_timezone": GRID_TIMEZONE,
        "open_request": open_request,
    }


async def join_slot(
    db: AsyncSession, student_id: int, slot_id: int
) -> dict[str, Any]:
    """Записать ученика в слот — с проверками, которые нельзя доверить экрану.

    Блокировка строки слота (`FOR UPDATE`) держится до конца записи: без неё
    два человека, одновременно нажавшие «записаться» на последнее место, оба
    прошли бы проверку «девять из десяти» и в слоте стало бы одиннадцать.
    """
    pref = await schedule_preference_service.get_preference(db, student_id)
    if not pref["is_audience"]:
        raise DomainError(
            "Запись на занятия открыта тем, кто продолжает учиться. "
            "Если это ошибка — напишите преподавателю.",
            status_code=403,
        )
    if not pref["is_filled"]:
        raise DomainError(
            "Сначала расскажите, когда вам удобно заниматься, — по этим "
            "ответам мы подбираем время.",
            status_code=409,
        )

    # Блокировка по ученику: два одновременных нажатия по РАЗНЫМ слотам берут
    # разные строки `lesson_slot`, поэтому блокировка слота их не сериализует —
    # оба прошли бы проверку «занятий не больше, чем просил», и человек получил
    # бы лишнее занятие, то есть лишнее начисление. Ключ снимается вместе с
    # транзакцией.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _BOOKING_LOCK_NAMESPACE + student_id},
    )

    row = (
        await db.execute(
            text(
                "SELECT id, weekday, start_time, is_active, active_until "
                "  FROM lesson_slot WHERE id = :id FOR UPDATE"
            ),
            {"id": slot_id},
        )
    ).first()
    if row is None or not bool(row[3]):
        raise DomainError("Такого занятия нет или оно отменено", status_code=404)

    weekday, start_time, active_until = int(row[1]), row[2], row[4]
    today = _today_moscow()
    if not in_grid((weekday, start_time)) or not slot_is_alive(
        weekday, active_until, today
    ):
        raise DomainError(
            "Это время больше не действует — выберите другое из списка",
            status_code=409,
        )

    count = int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM lesson_slot_student "
                    " WHERE slot_id = :id AND is_active"
                ),
                {"id": slot_id},
            )
        ).scalar()
        or 0
    )
    already = (
        await db.execute(
            text(
                "SELECT 1 FROM lesson_slot_student "
                " WHERE slot_id = :id AND student_id = :sid AND is_active"
            ),
            {"id": slot_id, "sid": student_id},
        )
    ).first()
    if already is not None:
        raise DomainError("Вы уже занимаетесь в это время", status_code=409)

    if not is_bookable_count(count):
        raise DomainError(
            "В это время группа уже набралась. Выберите другое время или "
            "нажмите «Не нашёл подходящее время» — методист подберёт вам час.",
            status_code=409,
        )

    # Своё расписание считаем по слотам, которые доживут до занятия: старые
    # летние слоты (действуют до 30 августа) не должны мешать новому ученику
    # выбрать осеннее время.
    mine = await _load_slots(db, student_id)
    my_alive = [
        s
        for s in mine
        if s["is_mine"] and slot_is_alive(s["weekday"], s["active_until"], today)
    ]
    if any(s["weekday"] == weekday and s["start_time"] == start_time for s in my_alive):
        raise DomainError("Вы уже занимаетесь в это время", status_code=409)

    if len(my_alive) >= pref["lessons_per_week"]:
        raise DomainError(
            f"Вы просили {pref['lessons_per_week']} занятий в неделю и уже выбрали "
            "столько. Чтобы записаться ещё, сначала измените пожелания.",
            status_code=409,
        )

    await lesson_calendar_service.add_slot_participant(
        db, slot_id, student_id, added_by=student_id
    )
    logger.info(
        "tsk-674: ученик %s записался в слот %s (%s), в слоте станет %s",
        student_id, slot_id, _hour_label(weekday, start_time), count + 1,
    )

    # Человек нашёл время сам — заявка методисту больше не нужна. Закрываем её
    # своей формулировкой, чтобы очередь не копила разобравшихся.
    await _resolve_open_request(
        db,
        student_id,
        note=f"Ученик записался сам: {_hour_label(weekday, start_time)} МСК",
        resolved_by=None,
    )
    await db.commit()

    return await get_bookable(db, student_id)


# ───────────────────── заявка «не нашёл подходящее время» ────────────────────


def _request_row_to_read(row: Any) -> ScheduleSlotRequestRead:
    raw_hours = row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]")
    return ScheduleSlotRequestRead(
        id=int(row[0]),
        student_id=int(row[1]),
        comment=row[2],
        lessons_per_week=int(row[3]),
        hours=[
            SchedulePreferenceHour(
                weekday=h["weekday"],
                start_time=time.fromisoformat(h["start_time"]),
                kind=h["kind"],
            )
            for h in raw_hours
        ],
        status=row[5],
        resolution_note=row[6],
        resolved_by=row[7],
        resolved_at=row[8],
        created_at=row[9],
        updated_at=row[10],
        full_name=row[11] if len(row) > 11 else None,
        email=row[12] if len(row) > 12 else None,
        timezone=row[13] if len(row) > 13 else None,
        current_slots=list(row[14] or []) if len(row) > 14 else [],
    )


_REQUEST_SELECT = """
    SELECT r.id, r.student_id, r.comment, r.lessons_per_week, r.hours, r.status,
           r.resolution_note, r.resolved_by, r.resolved_at, r.created_at,
           r.updated_at, u.full_name, u.email, u.timezone,
           COALESCE(slots.labels, ARRAY[]::text[]) AS current_slots
      FROM schedule_slot_request r
      JOIN users u ON u.id = r.student_id
      LEFT JOIN LATERAL (
          SELECT array_agg(
                   CASE ls.weekday
                     WHEN 0 THEN 'пн' WHEN 1 THEN 'вт' WHEN 2 THEN 'ср'
                     WHEN 3 THEN 'чт' WHEN 4 THEN 'пт' WHEN 5 THEN 'сб'
                     ELSE 'вс' END || ' ' || to_char(ls.start_time, 'HH24:MI')
                   ORDER BY ls.weekday, ls.start_time
                 ) AS labels
            FROM lesson_slot_student lss
            JOIN lesson_slot ls ON ls.id = lss.slot_id
           WHERE lss.student_id = r.student_id AND lss.is_active AND ls.is_active
      ) slots ON TRUE
"""


async def get_open_request(
    db: AsyncSession, student_id: int
) -> Optional[ScheduleSlotRequestRead]:
    """Открытая заявка ученика, если он уже просил помощи."""
    row = (
        await db.execute(
            text(
                _REQUEST_SELECT
                + " WHERE r.student_id = :sid AND r.status = 'open' LIMIT 1"
            ),
            {"sid": student_id},
        )
    ).first()
    return _request_row_to_read(row) if row is not None else None


async def _resolve_open_request(
    db: AsyncSession,
    student_id: int,
    *,
    note: str,
    resolved_by: Optional[int],
) -> None:
    """Закрыть открытую заявку ученика (если она есть). Без коммита."""
    await db.execute(
        text(
            "UPDATE schedule_slot_request "
            "   SET status = 'resolved', resolution_note = COALESCE(resolution_note, :note), "
            "       resolved_by = :by, resolved_at = now(), updated_at = now() "
            " WHERE student_id = :sid AND status = 'open'"
        ),
        {"sid": student_id, "note": note, "by": resolved_by},
    )


async def create_request(
    db: AsyncSession, student_id: int, comment: Optional[str]
) -> ScheduleSlotRequestRead:
    """Кнопка «Не нашёл подходящее время»: заявка методисту.

    Повторное нажатие не плодит вторую заявку — оно обновляет открытую и
    напоминает методисту ещё раз. Ограничения частоты здесь нет намеренно, по
    тому же основанию, что и у сигнала преподавателя (tsk-572): это осознанное
    действие человека, а не выстрел расписания, и придержать его значит
    потерять момент, когда он готов был рассказать, что ему не подходит.
    """
    pref = await schedule_preference_service.get_preference(db, student_id)
    if not pref["is_audience"]:
        raise DomainError(
            "Подбор времени доступен тем, кто продолжает учиться.", status_code=403
        )

    snapshot = [
        {
            "weekday": h.weekday,
            "start_time": h.start_time.strftime("%H:%M"),
            "kind": h.kind,
        }
        for h in pref["hours"]
    ]

    request_id = (
        await db.execute(
            text(
                """
                INSERT INTO schedule_slot_request
                       (student_id, comment, lessons_per_week, hours, status)
                VALUES (:sid, :comment, :lpw, CAST(:hours AS jsonb), 'open')
                ON CONFLICT (student_id) WHERE status = 'open'
                DO UPDATE SET comment          = EXCLUDED.comment,
                              lessons_per_week = EXCLUDED.lessons_per_week,
                              hours            = EXCLUDED.hours,
                              updated_at       = now()
                RETURNING id
                """
            ),
            {
                "sid": student_id,
                "comment": (comment or "").strip() or None,
                "lpw": pref["lessons_per_week"],
                "hours": json.dumps(snapshot, ensure_ascii=False),
            },
        )
    ).scalar_one()

    await _notify_methodists(
        db,
        request_id=int(request_id),
        student_id=student_id,
        student_name=(
            await db.execute(
                text("SELECT full_name FROM users WHERE id = :id"), {"id": student_id}
            )
        ).scalar(),
        lessons_per_week=pref["lessons_per_week"],
        hours=pref["hours"],
        comment=(comment or "").strip() or None,
    )
    await db.commit()

    logger.info(
        "tsk-674: ученик %s просит подобрать время (заявка %s), занятий в неделю %s",
        student_id, request_id, pref["lessons_per_week"],
    )
    request = await get_open_request(db, student_id)
    if request is None:  # pragma: no cover — только при гонке с закрытием
        raise DomainError("Заявку не удалось сохранить, попробуйте ещё раз", status_code=409)
    return request


async def _notify_methodists(
    db: AsyncSession,
    *,
    request_id: int,
    student_id: int,
    student_name: Optional[str],
    lessons_per_week: int,
    hours: list[SchedulePreferenceHour],
    comment: Optional[str],
) -> int:
    """Сказать методистам, что человек ждёт времени.

    Пожелания идут прямо в тексте: методист должен понять, о чём разговор, не
    открывая ничего, — иначе письмо превращается в «зайди и посмотри», а такие
    в этой школе оставались непрочитанными (tsk-591, tsk-652).
    """
    methodist_ids = [
        int(r[0])
        for r in (
            await db.execute(
                text(
                    "SELECT ur.user_id FROM user_roles ur "
                    "  JOIN roles r ON r.id = ur.role_id "
                    " WHERE r.name = 'methodist'"
                )
            )
        ).fetchall()
    ]
    if not methodist_ids:
        logger.warning(
            "tsk-674: заявку %s некому передать — методистов в системе нет", request_id
        )
        return 0

    preferred = [_hour_label(h.weekday, h.start_time) for h in hours if h.kind == "preferred"]
    possible = [_hour_label(h.weekday, h.start_time) for h in hours if h.kind == "possible"]
    who = student_name or f"ученик #{student_id}"

    lines = [f"{who} не нашёл подходящего времени в расписании."]
    if comment:
        lines.append(f"Своими словами: {comment}")
    lines.append(f"Занятий в неделю нужно: {lessons_per_week}.")
    lines.append(
        "Желательные часы: " + (", ".join(preferred) if preferred else "не выбраны")
    )
    if possible:
        lines.append("Возможные часы: " + ", ".join(possible))
    lines.append("Разберите заявку в кабинете: Расписание → Просят другое время.")

    payload = {
        "request_id": request_id,
        "student_id": student_id,
        "student_name": student_name,
        "lessons_per_week": lessons_per_week,
        "preferred": preferred,
        "possible": possible,
        # Слова ученика лежат в payload, а не только в теле: список эскалаций
        # (`GET /methodist/escalations/pending`) тела не отдаёт вовсе, и бот
        # методиста без этого поля пересказал бы заявку без самого главного.
        "comment": comment,
        "trigger": "schedule_slot_not_found",
    }
    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind=REQUEST_KIND,
            title="Ученик просит подобрать время занятий",
            content="\n".join(lines),
            payload=payload,
            created_by=student_id,
        )

    await audit_service.log_event(
        db,
        audit_service.METHODIST_ESCALATION_TRIGGERED,
        user_id=student_id,
        details={**payload, "methodist_count": len(methodist_ids)},
    )
    return len(methodist_ids)


async def list_requests(
    db: AsyncSession, *, status: Optional[str] = "open", limit: int = 100
) -> dict[str, Any]:
    """Очередь заявок у методиста: открытые сверху, свежие первыми."""
    where = ""
    params: dict[str, Any] = {"lim": limit}
    if status in ("open", "resolved"):
        where = " WHERE r.status = :status"
        params["status"] = status

    rows = (
        await db.execute(
            text(
                _REQUEST_SELECT
                + where
                + " ORDER BY (r.status = 'open') DESC, r.created_at DESC LIMIT :lim"
            ),
            params,
        )
    ).fetchall()

    open_count = int(
        (
            await db.execute(
                text("SELECT count(*) FROM schedule_slot_request WHERE status = 'open'")
            )
        ).scalar()
        or 0
    )
    return {
        "items": [_request_row_to_read(r) for r in rows],
        "open_count": open_count,
    }


async def resolve_request(
    db: AsyncSession,
    request_id: int,
    *,
    resolution_note: Optional[str],
    resolved_by: Optional[int],
) -> ScheduleSlotRequestRead:
    """Методист разобрал заявку."""
    row = (
        await db.execute(
            text(
                "UPDATE schedule_slot_request "
                "   SET status = 'resolved', resolution_note = :note, "
                "       resolved_by = :by, resolved_at = now(), updated_at = now() "
                " WHERE id = :id RETURNING student_id"
            ),
            {
                "id": request_id,
                "note": (resolution_note or "").strip() or None,
                "by": resolved_by,
            },
        )
    ).first()
    if row is None:
        raise DomainError("Такой заявки нет", status_code=404)
    await db.commit()

    fresh = (
        await db.execute(
            text(_REQUEST_SELECT + " WHERE r.id = :id"), {"id": request_id}
        )
    ).first()
    logger.info("tsk-674: заявка %s разобрана методистом %s", request_id, resolved_by)
    return _request_row_to_read(fresh)
