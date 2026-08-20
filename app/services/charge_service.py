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
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pricing_service

logger = logging.getLogger(__name__)

__all__ = [
    "month_start",
    "next_month",
    "lesson_counts_for_month",
    "lesson_counts_for_period",
    "recalculate_student_group",
    "recalculate_month",
    "recalculate_for_student",
    "recalculate_open_months_for_student",
    "recalculate_open_months_for_group",
    "list_charges",
    "set_manual_amount",
    "clear_manual_amount",
    "close_month",
    "reopen_month",
    "list_overrides",
    "set_price_override",
    "clear_price_override",
    "ChargeCounts",
    "charge_total_minor",
]


def month_start(day: date) -> date:
    """Первое число месяца — период начисления, а не дата события."""
    return day.replace(day=1)


def charge_total_minor(
    *, calculated_minor: int, manual_minor: Optional[int], adjustments_minor: int
) -> int:
    """Итог месяца: ручная сумма побеждает расчётную, поверх — поправки.

    Единственное место этой формулы (tsk-010). Раньше `COALESCE(manual,
    calculated) + adjustments` дублировалась как raw SQL в `payment_service`,
    `payment_reminder_service`, `payment_access_service` и как Python-код здесь
    же и в `payment_service.list_student_charges` — три места денежного контура
    (список платежей, напоминание о просрочке, проверка блокировки) должны
    видеть один и тот же приоритет «ручная цена важнее расчётной», а не свою
    копию правила.
    """
    base = manual_minor if manual_minor is not None else calculated_minor
    return base + adjustments_minor


@dataclass
class ChargeCounts:
    """Сколько занятий период предполагал и сколько из них не оплачивается.

    Вычетов два, и они независимы: перерыв (ученик есть, но не ходит) и
    «ещё не пришёл» (расписание месяца существует, но ученика в нём в эти дни
    ещё не было). Пересечься они не могут: `on_break` считается только среди
    дней от прихода и дальше, иначе новичок, которому сразу оформили перерыв,
    получил бы двойной вычет за один и тот же день.

    `expected` — знаменатель доли — остаётся месячным при любых вычетах: экран
    начислений должен показывать и «занятий в месяце», и сколько из них выпало,
    иначе сумма перестаёт объясняться (tsk-630).
    """

    expected: int
    on_break: int
    #: Занятий на днях ДО постановки ученика в расписание (tsk-630).
    not_started: int = 0

    @property
    def billable(self) -> int:
        """Занятий, за которые берут деньги. Ниже нуля не опускается."""
        return max(self.expected - self.on_break - self.not_started, 0)


async def lesson_counts_for_month(
    db: AsyncSession, *, student_id: int, period: date
) -> ChargeCounts:
    """Занятий в месяце по постоянному расписанию и сколько попало в перерыв.

    Тонкая обёртка над :func:`lesson_counts_for_period` с границами
    календарного месяца — денежный контур работает только помесячно
    (CHECK `date_trunc('month', period) = period` на `student_monthly_charge`).
    """
    last_day = next_month(period) - timedelta(days=1)
    return await lesson_counts_for_period(
        db, student_id=student_id, period_from=period, period_to=last_day
    )


async def lesson_counts_for_period(
    db: AsyncSession, *, student_id: int, period_from: date, period_to: date
) -> ChargeCounts:
    """Занятий за ПРОИЗВОЛЬНЫЙ период по постоянному расписанию, сколько из них
    попало в перерыв и сколько пришлось на дни до прихода ученика (границы
    включительные с обеих сторон).

    В слоте `weekday` считает от нуля-понедельника (проверено на живых данных:
    слот 0 → ISODOW 1). Считается пара «день × слот», поэтому два слота в один
    день дают два занятия, а не одно.

    **Приход ученика (tsk-630)** — самая ранняя постановка в расписание
    (`lesson_slot_student.created_at`), по МСК и по ВСЕМ привязкам, включая
    снятые. Три решения, каждое проверено на боевых данных августа 2026:

    * не дата подписки — 39 из 41 подписки заведены одним днём 08.08 при
      переезде на систему тарифов (tsk-301), это дата миграции, а не прихода
      человека; доля по ней срезала бы первую неделю августа всем 37 ученикам;
    * не первое занятие — генератор занятий заполняет календарь вперёд
      неравномерно, и трое учеников с расписанием ещё с июля потеряли бы по
      занятию из-за него, а не по существу (та же причина, по которой весь этот
      счёт идёт от постоянного расписания, см. модуль);
    * включая снятые привязки — иначе ученику, которому пересоздали слот,
      месяц срезался бы как новичку.

    tsk-556: вынесено из `lesson_counts_for_month` — дашборду нужен тот же счёт
    за хвост периода, до которого генератор занятий ещё не дошёл. Хвост всегда
    в будущем, поэтому `not_started` там ноль по построению.
    """
    row = (
        await db.execute(
            text(
                """
                WITH days AS (
                    -- CAST(...), а не :period_from::date — постфиксное приведение
                    -- на параметре asyncpg не разбирает («ошибка синтаксиса»).
                    SELECT d::date AS day
                      FROM generate_series(
                               CAST(:period_from AS date),
                               CAST(:period_to AS date),
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
                ),
                joined AS (
                    -- День прихода: самая ранняя постановка в расписание. NULL,
                    -- когда привязок нет вовсе, — тогда вычета «ещё не пришёл»
                    -- нет, и месяц считается как раньше.
                    SELECT min((lss.created_at AT TIME ZONE 'Europe/Moscow')::date)
                               AS started_on
                      FROM lesson_slot_student lss
                     WHERE lss.student_id = :student_id
                )
                SELECT count(*) AS expected,
                       count(*) FILTER (
                           WHERE days.day < joined.started_on
                       ) AS not_started,
                       count(*) FILTER (
                           -- Только среди дней ОТ прихода: иначе новичок с
                           -- перерывом получил бы двойной вычет за один день.
                           WHERE (joined.started_on IS NULL
                                  OR days.day >= joined.started_on)
                             AND EXISTS (
                               SELECT 1 FROM student_break b
                                WHERE b.student_id = :student_id
                                  AND days.day BETWEEN b.starts_on AND b.ends_on
                           )
                       ) AS on_break
                  FROM days
                  JOIN slots ON (EXTRACT(ISODOW FROM days.day)::int - 1) = slots.weekday
                  CROSS JOIN joined
                """
            ),
            {
                "student_id": student_id,
                "period_from": period_from,
                "period_to": period_to,
            },
        )
    ).one()
    return ChargeCounts(
        expected=int(row.expected),
        on_break=int(row.on_break),
        not_started=int(row.not_started),
    )


async def _base_price_minor(
    db: AsyncSession, *, student_id: int, group_id: int, period: date
) -> Optional[int]:
    """База месяца: ручная цена группы, иначе расчёт по тарифу.

    Ручная цена НЕ пропорционируется перерывом — договорённость с человеком не
    должна тихо уезжать. Расчётная цена пропорционируется.

    `period` обязателен: расчёт берёт группу подписки, действовавшей на первое
    число месяца, а не сегодняшнюю (контракт прав §7, tsk-585).
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

    for student in await pricing_service.list_student_pricing(db, period=period):
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
    """Доля месяца за вычетом занятий, за которые денег не берут.

    Вычетов два и они складываются: перерыв и «ученик пришёл среди месяца»
    (tsk-630). Складываются именно суммой, а не максимумом: месяц, в который
    человек пришёл 12-го и с 20-го ушёл в перерыв, оплачивается только за
    промежуток между этими датами.

    Округляем ВНИЗ, то есть в пользу ученика: копейка спора не стоит, а
    предсказуемое направление округления стоит.
    """
    if counts.expected <= 0:
        # Делить не на что — расписания нет. Доля не применяется.
        return base_minor
    return base_minor * counts.billable // counts.expected


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
    base = await _base_price_minor(
        db, student_id=student_id, group_id=group_id, period=period
    )
    if base is None:
        # Считать больше не из чего (сняли ручную цену, курс перестал продаваться).
        # Открытую строку убираем: иначе она замрёт со старой суммой и останется
        # призрачным начислением, которое никто уже не пересчитает. Закрытые
        # месяцы не трогаем — это история, а не текущее состояние.
        #
        # tsk-010: месяц с принятым платежом не удаляем. Иначе вместе со строкой
        # исчезли бы деньги, которые к ней привязаны, — а внешний ключ платежа
        # (ON DELETE RESTRICT) превратил бы это в ошибку посреди пересчёта.
        res = await db.execute(
            text(
                "DELETE FROM student_monthly_charge ch "
                "WHERE ch.student_id = :s AND ch.group_id = :g AND ch.period = :p "
                "  AND ch.status = 'open' "
                "  AND NOT EXISTS (SELECT 1 FROM student_payment p "
                "                   WHERE p.student_id = ch.student_id "
                "                     AND p.group_id = ch.group_id "
                "                     AND p.period = ch.period)"
            ),
            {"s": student_id, "g": group_id, "p": period},
        )
        if res.rowcount == 0:
            logger.info(
                "Начисление %s/%s за %s оставлено: считать не из чего, но по нему есть платежи",
                student_id,
                group_id,
                period,
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
                        expected_lessons, break_lessons, not_started_lessons)
                VALUES (:s, :g, :p, :calc, :exp, :brk, :nst)
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
                "nst": counts.not_started,
            },
        )
        return calculated

    if existing.status == "open":
        await db.execute(
            text(
                "UPDATE student_monthly_charge "
                "SET calculated_minor = :calc, expected_lessons = :exp, "
                "    break_lessons = :brk, not_started_lessons = :nst, "
                "    updated_at = now() "
                "WHERE id = :id"
            ),
            {
                "id": existing.id,
                "calc": calculated,
                "exp": counts.expected,
                "brk": counts.on_break,
                "nst": counts.not_started,
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
    base = await _base_price_minor(
        db, student_id=student_id, group_id=group_id, period=period
    )
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
                    expected_lessons, break_lessons, not_started_lessons)
            VALUES (:s, :g, :p, :calc, :exp, :brk, :nst)
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
            "nst": counts.not_started,
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


async def recalculate_open_months_for_group(
    db: AsyncSession, *, group_id: int
) -> int:
    """Пересчитать открытые месяцы всех учеников тарифной группы (tsk-517).

    Зовётся после правки варианта тарифа. Правка цены или оси меняет расчёт для
    каждого, кто на эту группу попадает, — без пересчёта суммы остались бы
    старыми до следующего ручного нажатия, и экран показывал бы неправду.

    Возвращает число затронутых учеников.
    """
    students = (
        await db.execute(
            text(
                """
                SELECT DISTINCT uc.user_id AS student_id
                  FROM user_courses uc
                  JOIN course_pricing cp ON cp.course_id = uc.course_id
                                        AND cp.sale_status = 'paid'
                  JOIN users u ON u.id = uc.user_id AND u.is_active
                 WHERE uc.is_active AND cp.group_id = :g
                UNION
                SELECT DISTINCT ch.student_id
                  FROM student_monthly_charge ch
                 WHERE ch.group_id = :g AND ch.status = 'open'
                UNION
                -- tsk-301: ученик может попадать в группу подписки, не будучи
                -- зачисленным ни на один её курс (Self и AI курсов не имеют).
                SELECT DISTINCT s.student_id
                  FROM student_subscription s
                  JOIN users u ON u.id = s.student_id AND u.is_active
                 WHERE s.ends_on IS NULL AND s.pricing_group_id = :g
                UNION
                -- tsk-585: текущий месяц считается по группе, действовавшей на
                -- первое число. Ученик, ушедший с этой группы среди месяца, всё
                -- ещё считается по ней — правка её тарифа касается и его.
                SELECT DISTINCT s.student_id
                  FROM student_subscription s
                  JOIN users u ON u.id = s.student_id AND u.is_active
                 WHERE s.pricing_group_id = :g
                   AND s.starts_on <= :p
                   AND (s.ends_on IS NULL OR s.ends_on >= :p)
                """
            ),
            {"g": group_id, "p": month_start(date.today())},
        )
    ).all()
    for row in students:
        await recalculate_open_months_for_student(db, student_id=row.student_id)
    return len(students)


async def recalculate_for_student(
    db: AsyncSession, *, student_id: int, period: Optional[date] = None
) -> None:
    """Пересчитать все группы одного ученика за месяц (по умолчанию текущий).

    Точка входа для автопересчёта: её зовут смена расписания и правка перерыва.
    """
    period = month_start(period or date.today())
    # tsk-301: группа берётся из подписки, если она есть, иначе из проданных
    # курсов (прежнее поведение). tsk-585: из подписки, действовавшей на ПЕРВОЕ
    # ЧИСЛО периода, — смена тарифа посреди месяца текущий месяц не переписывает.
    target_groups = await pricing_service.billing_group_ids(
        db, student_id=student_id, period=period
    )

    # К целевым добавляем группы, по которым у ученика УЖЕ есть открытая строка.
    # Без этого смена тарифа оставила бы прежнее начисление нетронутым: цикл
    # прошёл бы только по новой группе, а старая строка замерла бы со своей
    # суммой навсегда — её больше никто не пересчитывает. `recalculate_student_group`
    # такую строку удалит сам, как только увидит, что считать её не из чего.
    stale = (
        await db.execute(
            text(
                "SELECT DISTINCT group_id FROM student_monthly_charge "
                " WHERE student_id = :s AND period = :p AND status = 'open'"
            ),
            {"s": student_id, "p": period},
        )
    ).all()
    group_ids = sorted({*target_groups, *(int(r.group_id) for r in stale)})

    for group_id in group_ids:
        # Вложенная транзакция на группу — как в `recalculate_month`. Эту точку
        # входа зовут смена расписания и правка перерыва, то есть она работает
        # среди дня, когда идут платежи: платёж, пришедший ровно между проверкой
        # «нет оплат» и удалением строки месяца (tsk-010), иначе уронил бы
        # действие методиста ошибкой сервера.
        try:
            async with db.begin_nested():
                await recalculate_student_group(
                    db, student_id=student_id, group_id=group_id, period=period
                )
        except IntegrityError:
            logger.warning(
                "Пересчёт %s/%s за %s пропущен: строка изменилась во время расчёта",
                student_id,
                group_id,
                period,
                exc_info=True,
            )
    await db.commit()


async def recalculate_month(db: AsyncSession, *, period: date) -> int:
    """Пересчитать месяц по всем ученикам. Возвращает число затронутых строк.

    tsk-630: к парам «ученик × группа» из резолвера обязательно добираются те,
    по которым в месяце УЖЕ есть открытая строка. Без этого смена тарифа плодила
    ДВА начисления за один месяц: цикл проходил только по новой группе, а строка
    старой оставалась нетронутой — её здесь больше никто не пересчитывает, и
    удалить её как «считать не из чего» было некому. На боевых данных августа
    2026 перевод пятерых учеников на другой тариф с последующим нажатием
    «Пересчитать месяц» давал 45 строк вместо 41 и +22 000 рублей из воздуха.
    `recalculate_for_student` этот добор делал с самого начала — здесь его не
    было.
    """
    period = month_start(period)
    students = await pricing_service.list_student_pricing(db, period=period)
    targets: list[tuple[int, int]] = [
        (student.student_id, group.group_id)
        for student in students
        for group in student.groups
    ]
    stale = (
        await db.execute(
            text(
                "SELECT student_id, group_id FROM student_monthly_charge "
                " WHERE period = :p AND status = 'open'"
            ),
            {"p": period},
        )
    ).all()
    seen = set(targets)
    for row in stale:
        pair = (int(row.student_id), int(row.group_id))
        if pair not in seen:
            seen.add(pair)
            targets.append(pair)

    touched = 0
    for student_id, group_id in targets:
        # Каждая пара — в своей вложенной транзакции: платёж, пришедший ровно
        # между проверкой «нет оплат» и удалением строки месяца (tsk-010),
        # уронил бы иначе пересчёт ВСЕГО месяца, а не одну строку.
        try:
            async with db.begin_nested():
                await recalculate_student_group(
                    db,
                    student_id=student_id,
                    group_id=group_id,
                    period=period,
                )
        except IntegrityError:
            logger.warning(
                "Пересчёт %s/%s за %s пропущен: строка изменилась во время расчёта",
                student_id,
                group_id,
                period,
                exc_info=True,
            )
            continue
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
                       ch.not_started_lessons,
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
        total = charge_total_minor(
            calculated_minor=r.calculated_minor,
            manual_minor=r.manual_minor,
            adjustments_minor=int(r.adjustments_minor),
        )
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
                "total_minor": total,
                "expected_lessons": r.expected_lessons,
                "break_lessons": r.break_lessons,
                "not_started_lessons": r.not_started_lessons,
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
