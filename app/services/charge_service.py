"""tsk-511/512/513 — помесячные начисления, перерывы в деньгах, ручная цена.

Почему база месяца берётся из ПОСТОЯННОГО расписания, а не из сгенерированных
занятий: на 2026-08-01 занятия существовали только на три недели вперёд
(2026-07-26 … 08-14). Считай мы долю перерыва по ним — сумма зависела бы от того,
когда в последний раз крутили генератор, и менялась бы сама собой. Поэтому дни
месяца сверяются с днями недели активных слотов ученика.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pricing_service

logger = logging.getLogger(__name__)

__all__ = [
    "month_start",
    "next_month",
    "lesson_counts_for_month",
    "recalculate_student_group",
    "recalculate_month",
    "recalculate_for_student",
    "recalculate_open_months_for_student",
    "list_charges",
    "set_manual_amount",
    "clear_manual_amount",
    "close_month",
    "reopen_month",
    "list_overrides",
    "set_price_override",
    "clear_price_override",
    "ChargeCounts",
]


def month_start(day: date) -> date:
    """Первое число месяца — период начисления, а не дата события."""
    return day.replace(day=1)


@dataclass
class ChargeCounts:
    """Сколько занятий месяц предполагал и сколько из них съел перерыв."""

    expected: int
    on_break: int


async def lesson_counts_for_month(
    db: AsyncSession, *, student_id: int, period: date
) -> ChargeCounts:
    """Занятий в месяце по постоянному расписанию и сколько попало в перерыв.

    В слоте `weekday` считает от нуля-понедельника (проверено на живых данных:
    слот 0 → ISODOW 1). Считается пара «день × слот», поэтому два слота в один
    день дают два занятия, а не одно.
    """
    row = (
        await db.execute(
            text(
                """
                WITH days AS (
                    -- CAST(...), а не :period::date — постфиксное приведение
                    -- на параметре asyncpg не разбирает («ошибка синтаксиса»).
                    SELECT d::date AS day
                      FROM generate_series(
                               CAST(:period AS date),
                               CAST(:period AS date) + INTERVAL '1 month' - INTERVAL '1 day',
                               INTERVAL '1 day'
                           ) AS d
                ),
                slots AS (
                    SELECT ls.weekday
                      FROM lesson_slot_student lss
                      JOIN lesson_slot ls ON ls.id = lss.slot_id
                     WHERE lss.student_id = :student_id
                       AND lss.is_active
                       AND ls.is_active
                )
                SELECT count(*) AS expected,
                       count(*) FILTER (
                           WHERE EXISTS (
                               SELECT 1 FROM student_break b
                                WHERE b.student_id = :student_id
                                  AND days.day BETWEEN b.starts_on AND b.ends_on
                           )
                       ) AS on_break
                  FROM days
                  JOIN slots ON (EXTRACT(ISODOW FROM days.day)::int - 1) = slots.weekday
                """
            ),
            {"student_id": student_id, "period": period},
        )
    ).one()
    return ChargeCounts(expected=int(row.expected), on_break=int(row.on_break))


async def _base_price_minor(
    db: AsyncSession, *, student_id: int, group_id: int
) -> Optional[int]:
    """База месяца: ручная цена группы, иначе расчёт по тарифу.

    Ручная цена НЕ пропорционируется перерывом — договорённость с человеком не
    должна тихо уезжать. Расчётная цена пропорционируется.
    """
    override = (
        await db.execute(
            text(
                "SELECT price_minor FROM student_price_override "
                "WHERE student_id = :s AND group_id = :g"
            ),
            {"s": student_id, "g": group_id},
        )
    ).first()
    if override is not None:
        return int(override.price_minor)

    for student in await pricing_service.list_student_pricing(db):
        if student.student_id != student_id:
            continue
        for group in student.groups:
            if group.group_id == group_id:
                return group.price_minor
    return None


async def _has_override(db: AsyncSession, *, student_id: int, group_id: int) -> bool:
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM student_price_override "
                "WHERE student_id = :s AND group_id = :g"
            ),
            {"s": student_id, "g": group_id},
        )
    ).first()
    return row is not None


def _prorate(base_minor: int, counts: ChargeCounts) -> int:
    """Доля месяца за вычетом пропущенных занятий.

    Округляем ВНИЗ, то есть в пользу ученика: копейка спора не стоит, а
    предсказуемое направление округления стоит.
    """
    if counts.expected <= 0:
        # Делить не на что — расписания нет. Доля не применяется.
        return base_minor
    attended = max(counts.expected - counts.on_break, 0)
    return base_minor * attended // counts.expected


async def recalculate_student_group(
    db: AsyncSession, *, student_id: int, group_id: int, period: date
) -> Optional[int]:
    """Пересчитать один месяц одного ученика по одной группе.

    Открытый месяц переписывается. Закрытый НЕ трогается: если пересчёт даёт
    другое число, разница уходит поправкой в следующий открытый месяц — так
    договорённость, уже названную человеку, не переписывают задним числом.

    Возвращает итог месяца в копейках либо None, если считать не из чего.
    """
    period = month_start(period)
    base = await _base_price_minor(db, student_id=student_id, group_id=group_id)
    if base is None:
        # Считать больше не из чего (сняли ручную цену, курс перестал продаваться).
        # Открытую строку убираем: иначе она замрёт со старой суммой и останется
        # призрачным начислением, которое никто уже не пересчитает. Закрытые
        # месяцы не трогаем — это история, а не текущее состояние.
        await db.execute(
            text(
                "DELETE FROM student_monthly_charge "
                "WHERE student_id = :s AND group_id = :g AND period = :p "
                "  AND status = 'open'"
            ),
            {"s": student_id, "g": group_id, "p": period},
        )
        return None

    counts = await lesson_counts_for_month(db, student_id=student_id, period=period)
    calculated = (
        base
        if await _has_override(db, student_id=student_id, group_id=group_id)
        else _prorate(base, counts)
    )

    existing = (
        await db.execute(
            text(
                "SELECT id, status, calculated_minor, manual_minor "
                "FROM student_monthly_charge "
                "WHERE student_id = :s AND group_id = :g AND period = :p"
            ),
            {"s": student_id, "g": group_id, "p": period},
        )
    ).first()

    if existing is None:
        await db.execute(
            text(
                """
                INSERT INTO student_monthly_charge
                       (student_id, group_id, period, calculated_minor,
                        expected_lessons, break_lessons)
                VALUES (:s, :g, :p, :calc, :exp, :brk)
                ON CONFLICT (student_id, group_id, period) DO NOTHING
                """
            ),
            {
                "s": student_id,
                "g": group_id,
                "p": period,
                "calc": calculated,
                "exp": counts.expected,
                "brk": counts.on_break,
            },
        )
        return calculated

    if existing.status == "open":
        await db.execute(
            text(
                "UPDATE student_monthly_charge "
                "SET calculated_minor = :calc, expected_lessons = :exp, "
                "    break_lessons = :brk, updated_at = now() "
                "WHERE id = :id"
            ),
            {
                "id": existing.id,
                "calc": calculated,
                "exp": counts.expected,
                "brk": counts.on_break,
            },
        )
        return (
            existing.manual_minor if existing.manual_minor is not None else calculated
        )

    # Месяц закрыт. Расхождение переносим вперёд, а не переписываем прошлое.
    frozen = (
        existing.manual_minor if existing.manual_minor is not None else existing.calculated_minor
    )
    delta = calculated - frozen
    if delta != 0:
        await _carry_forward(
            db,
            student_id=student_id,
            group_id=group_id,
            origin_period=period,
            delta_minor=delta,
        )
    return frozen


async def _carry_forward(
    db: AsyncSession,
    *,
    student_id: int,
    group_id: int,
    origin_period: date,
    delta_minor: int,
) -> None:
    """Перенести расхождение закрытого месяца в следующий.

    Повторный пересчёт того же закрытого месяца не должен плодить поправки:
    частичный уникальный индекс по `origin_period` ловит это на уровне БД, а
    здесь мы обновляем уже существующий перенос, если сумма уточнилась.
    """
    target = next_month(origin_period)
    # Месяца-получателя может ещё не быть: тогда поправка легла бы в пустоту —
    # список начислений строится ОТ строк месяца, и перенос был бы невидим.
    await _ensure_charge_row(db, student_id=student_id, group_id=group_id, period=target)
    reason = f"Перенос за {origin_period:%m.%Y}: месяц был закрыт, расчёт изменился"
    await db.execute(
        text(
            """
            INSERT INTO charge_adjustment
                   (student_id, group_id, period, amount_minor, reason,
                    source, origin_period)
            VALUES (:s, :g, :p, :amt, :reason, 'carry_forward', :origin)
            ON CONFLICT (student_id, group_id, period, origin_period)
                WHERE source = 'carry_forward'
            DO UPDATE SET amount_minor = EXCLUDED.amount_minor,
                          reason = EXCLUDED.reason
            """
        ),
        {
            "s": student_id,
            "g": group_id,
            "p": target,
            "amt": delta_minor,
            "reason": reason,
            "origin": origin_period,
        },
    )


async def _ensure_charge_row(
    db: AsyncSession, *, student_id: int, group_id: int, period: date
) -> None:
    """Завести строку месяца, если её ещё нет.

    Намеренно без рекурсии в `recalculate_student_group`: цепочка закрытых
    месяцев не должна раскручиваться сама на себя.
    """
    base = await _base_price_minor(db, student_id=student_id, group_id=group_id)
    counts = await lesson_counts_for_month(db, student_id=student_id, period=period)
    calculated = 0 if base is None else (
        base
        if await _has_override(db, student_id=student_id, group_id=group_id)
        else _prorate(base, counts)
    )
    await db.execute(
        text(
            """
            INSERT INTO student_monthly_charge
                   (student_id, group_id, period, calculated_minor,
                    expected_lessons, break_lessons)
            VALUES (:s, :g, :p, :calc, :exp, :brk)
            ON CONFLICT (student_id, group_id, period) DO NOTHING
            """
        ),
        {
            "s": student_id,
            "g": group_id,
            "p": period,
            "calc": calculated,
            "exp": counts.expected,
            "brk": counts.on_break,
        },
    )


def next_month(period: date) -> date:
    """Следующий месяц — точка переноса поправок."""
    return date(period.year + (period.month // 12), (period.month % 12) + 1, 1)


async def recalculate_open_months_for_student(
    db: AsyncSession, *, student_id: int
) -> None:
    """Пересчитать ученику ВСЕ открытые месяцы, а не только текущий.

    Ручная цена действует бессрочно, поэтому её снятие обесценивает каждый
    незакрытый месяц. Пересчёт одного текущего оставил бы в остальных
    призрачные суммы, которые уже никто не тронет.
    """
    periods = (
        await db.execute(
            text(
                "SELECT DISTINCT period FROM student_monthly_charge "
                "WHERE student_id = :s AND status = 'open'"
            ),
            {"s": student_id},
        )
    ).all()
    targets = {row.period for row in periods}
    targets.add(month_start(date.today()))
    for target in sorted(targets):
        await recalculate_for_student(db, student_id=student_id, period=target)


async def recalculate_for_student(
    db: AsyncSession, *, student_id: int, period: Optional[date] = None
) -> None:
    """Пересчитать все группы одного ученика за месяц (по умолчанию текущий).

    Точка входа для автопересчёта: её зовут смена расписания и правка перерыва.
    """
    period = month_start(period or date.today())
    groups = (
        await db.execute(
            text(
                """
                SELECT DISTINCT cp.group_id
                  FROM user_courses uc
                  JOIN course_pricing cp ON cp.course_id = uc.course_id
                                        AND cp.sale_status = 'paid'
                 WHERE uc.user_id = :s AND uc.is_active
                """
            ),
            {"s": student_id},
        )
    ).all()
    for row in groups:
        await recalculate_student_group(
            db, student_id=student_id, group_id=row.group_id, period=period
        )
    await db.commit()


async def recalculate_month(db: AsyncSession, *, period: date) -> int:
    """Пересчитать месяц по всем ученикам. Возвращает число затронутых строк."""
    period = month_start(period)
    students = await pricing_service.list_student_pricing(db)
    touched = 0
    for student in students:
        for group in student.groups:
            await recalculate_student_group(
                db,
                student_id=student.student_id,
                group_id=group.group_id,
                period=period,
            )
            touched += 1
    await db.commit()
    return touched


async def list_charges(db: AsyncSession, *, period: date) -> list[dict]:
    """Начисления месяца со всей расшифровкой: из чего сложилась сумма.

    Отдаём и расчёт, и ручную сумму, и поправки по отдельности — иначе на экране
    останется одно число, по которому нельзя понять, почему оно такое.
    """
    period = month_start(period)
    rows = (
        await db.execute(
            text(
                """
                SELECT ch.id,
                       ch.student_id,
                       u.full_name,
                       ch.group_id,
                       pg.name                AS group_name,
                       ch.period,
                       ch.calculated_minor,
                       ch.manual_minor,
                       ch.expected_lessons,
                       ch.break_lessons,
                       ch.status,
                       ch.closed_at,
                       COALESCE(adj.total, 0) AS adjustments_minor,
                       adj.details            AS adjustment_details,
                       (ovr.price_minor IS NOT NULL) AS has_price_override,
                       ovr.price_minor        AS override_minor
                  FROM student_monthly_charge ch
                  JOIN users u ON u.id = ch.student_id
                  JOIN pricing_group pg ON pg.id = ch.group_id
                  LEFT JOIN student_price_override ovr
                         ON ovr.student_id = ch.student_id
                        AND ovr.group_id = ch.group_id
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total,
                               string_agg(a.reason || ' (' ||
                                          to_char(a.amount_minor / 100.0, 'FM999999990.00') ||
                                          ' руб.)', '; ' ORDER BY a.id) AS details
                          FROM charge_adjustment a
                         WHERE a.student_id = ch.student_id
                           AND a.group_id = ch.group_id
                           AND a.period = ch.period
                  ) adj ON TRUE
                 WHERE ch.period = :p
                 ORDER BY u.full_name, pg.name
                """
            ),
            {"p": period},
        )
    ).all()

    result: list[dict] = []
    for r in rows:
        base = r.manual_minor if r.manual_minor is not None else r.calculated_minor
        result.append(
            {
                "id": r.id,
                "student_id": r.student_id,
                "full_name": r.full_name,
                "group_id": r.group_id,
                "group_name": r.group_name,
                "period": r.period,
                "calculated_minor": r.calculated_minor,
                "manual_minor": r.manual_minor,
                "adjustments_minor": int(r.adjustments_minor),
                "adjustment_details": r.adjustment_details,
                "total_minor": base + int(r.adjustments_minor),
                "expected_lessons": r.expected_lessons,
                "break_lessons": r.break_lessons,
                "status": r.status,
                "closed_at": r.closed_at,
                "has_price_override": bool(r.has_price_override),
                "override_minor": r.override_minor,
            }
        )
    return result


async def set_manual_amount(
    db: AsyncSession, *, charge_id: int, amount_minor: int
) -> bool:
    """Поставить сумму месяца руками. Закрытый месяц не правится."""
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge SET manual_minor = :amt, updated_at = now() "
            "WHERE id = :id AND status = 'open'"
        ),
        {"id": charge_id, "amt": amount_minor},
    )
    await db.commit()
    return res.rowcount > 0


async def clear_manual_amount(db: AsyncSession, *, charge_id: int) -> bool:
    """Вернуться к расчёту: снять ручную сумму месяца."""
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge SET manual_minor = NULL, updated_at = now() "
            "WHERE id = :id AND status = 'open'"
        ),
        {"id": charge_id},
    )
    await db.commit()
    return res.rowcount > 0


async def close_month(db: AsyncSession, *, period: date, closed_by: int) -> int:
    """Закрыть месяц: суммы замирают, дальнейшие расхождения пойдут переносом."""
    period = month_start(period)
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge "
            "SET status = 'closed', closed_at = now(), closed_by = :by, updated_at = now() "
            "WHERE period = :p AND status = 'open'"
        ),
        {"p": period, "by": closed_by},
    )
    await db.commit()
    return res.rowcount


async def reopen_month(db: AsyncSession, *, period: date) -> int:
    """Открыть месяц обратно — на случай, если закрыли по ошибке."""
    period = month_start(period)
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge "
            "SET status = 'open', closed_at = NULL, closed_by = NULL, updated_at = now() "
            "WHERE period = :p AND status = 'closed'"
        ),
        {"p": period},
    )
    await db.commit()
    return res.rowcount


async def list_overrides(db: AsyncSession) -> list[dict]:
    """Ручные цены по ученикам — отдельным списком, чтобы их было видно все разом."""
    rows = (
        await db.execute(
            text(
                "SELECT o.id, o.student_id, u.full_name, o.group_id, pg.name AS group_name, "
                "       o.price_minor, o.note "
                "  FROM student_price_override o "
                "  JOIN users u ON u.id = o.student_id "
                "  JOIN pricing_group pg ON pg.id = o.group_id "
                " ORDER BY u.full_name, pg.name"
            )
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def set_price_override(
    db: AsyncSession,
    *,
    student_id: int,
    group_id: int,
    price_minor: int,
    note: Optional[str],
    created_by: Optional[int],
) -> None:
    """Назначить ученику цену руками. Повторный вызов правит существующую."""
    await db.execute(
        text(
            """
            INSERT INTO student_price_override
                   (student_id, group_id, price_minor, note, created_by)
            VALUES (:s, :g, :price, :note, :by)
            ON CONFLICT (student_id, group_id)
            DO UPDATE SET price_minor = EXCLUDED.price_minor,
                          note = EXCLUDED.note,
                          updated_at = now()
            """
        ),
        {
            "s": student_id,
            "g": group_id,
            "price": price_minor,
            "note": note,
            "by": created_by,
        },
    )
    await db.commit()


async def clear_price_override(
    db: AsyncSession, *, student_id: int, group_id: int
) -> bool:
    """Снять ручную цену — ученик возвращается к расчёту по тарифу."""
    res = await db.execute(
        text(
            "DELETE FROM student_price_override WHERE student_id = :s AND group_id = :g"
        ),
        {"s": student_id, "g": group_id},
    )
    await db.commit()
    return res.rowcount > 0
