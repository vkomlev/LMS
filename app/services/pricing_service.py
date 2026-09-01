"""Тарифы курсов и расчёт цены ученика (tsk-505).

Почему цена считается так, а не «цена у курса»: календарь LMS не знает о курсах
(ни `lesson_slot`, ни `lesson_occurrence` не имеют `course_id`), поэтому «сколько
раз в неделю ученик ходит на ЭТОТ курс» технически невыводимо — выводится только
«сколько раз в неделю ученик ходит вообще». Оператор 2026-08-01 закрепил из этого
модель: платит ученик, курс лишь относит его к тарифной ГРУППЕ, а внутри группы
цена берётся один раз (пара «Python для ЕГЭ» + «ЕГЭ по информатике» — один продукт).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.pricing import (
    CoursePricingRead,
    FrequencySource,
    PricingGroupRead,
    StudentGroupPricing,
    StudentPricingRead,
    TariffRead,
)

logger = logging.getLogger(__name__)

#: Колонки, которые правка вправе трогать (см. `_build_patch`).
_GROUP_PATCH_COLUMNS = frozenset({"name", "description", "is_active"})
_TARIFF_PATCH_COLUMNS = frozenset(
    {
        "name",
        "price_minor",
        "period",
        # Ось правится с tsk-517: она меняет смысл варианта, поэтому экран
        # предупреждает, а открытые месяцы после правки пересчитываются.
        "match_kind",
        "match_value",
        "is_default",
        "sort_order",
        "is_active",
    }
)

__all__ = [
    "list_groups",
    "create_group",
    "update_group",
    "delete_group",
    "create_tariff",
    "tariff_group_id",
    "update_tariff",
    "delete_tariff",
    "list_course_pricing",
    "is_root_course",
    "set_course_pricing",
    "list_student_pricing",
    "active_subscription_groups",
    "billing_group_ids",
    "resolve_attendance_frequency",
    "AttendanceFrequencyResolution",
]


# ------------------------------------------------- группа из подписки (tsk-301)


#: Строка подписки, действовавшая на первое число расчётного месяца. `DISTINCT ON`
#: с сортировкой по `starts_on DESC, id DESC` нужен из-за того, что смена тарифа
#: закрывает старую строку и открывает новую ОДНОЙ И ТОЙ ЖЕ датой
#: (`subscription_service.change_plan`): в день смены под условие попадают обе, и
#: выиграть обязана новая — смена ровно первого числа действует с этого же месяца.
_SUBSCRIPTION_AT_PERIOD_START = """
WITH at_start AS (
    SELECT DISTINCT ON (student_id) student_id, pricing_group_id
      FROM student_subscription
     WHERE starts_on <= :p AND (ends_on IS NULL OR ends_on >= :p)
     ORDER BY student_id, starts_on DESC, id DESC
),
current_row AS (
    SELECT student_id, pricing_group_id
      FROM student_subscription
     WHERE ends_on IS NULL
)
SELECT COALESCE(a.student_id, c.student_id)               AS student_id,
       COALESCE(a.pricing_group_id, c.pricing_group_id)   AS pricing_group_id
  FROM at_start a
  FULL JOIN current_row c ON c.student_id = a.student_id
"""


def reference_day(period: Optional[date], *, today: Optional[date] = None) -> Optional[date]:
    """Дата, на которую смотрят расписание при расчёте месяца (tsk-756).

    Сегодняшний день, прижатый к границам месяца. Считая ТЕКУЩИЙ месяц, смотрим
    на сегодня — прежнее поведение; считая прошедший — на его последний день, а
    не на сегодняшнюю сетку (из-за этого цена августа и уехала по сентябрьской
    частоте); считая будущий — на его первое число, то есть на сетку, с которой
    месяц начнётся.

    `None` на входе (витрина «как сейчас») даёт `None` на выходе — запрос сам
    подставит `CURRENT_DATE`.
    """
    if period is None:
        return None
    first_day = period.replace(day=1)
    last_day = date(
        first_day.year + (first_day.month // 12), (first_day.month % 12) + 1, 1
    ) - timedelta(days=1)
    # Сегодня, прижатое к границам месяца: текущий месяц → сегодня, прошедший →
    # его последний день, будущий → его первое число.
    return min(last_day, max(first_day, today or date.today()))


async def active_subscription_groups(
    db: AsyncSession, *, period: Optional[date] = None
) -> dict[int, Optional[int]]:
    """`student_id` → тарифная группа подписки.

    **Наличие ключа означает «подписка есть»**, а значение `None` — «денег нет
    вовсе» (Test, Demo, Выпускник). Различать это обязательно: отсутствие ключа
    возвращает ученика к прежнему поведению (группа из курса), а `None` деньги
    отменяет.

    `period` разводит две разные вещи (контракт прав §7, tsk-585):

    * **без периода** — строка, действующая СЕГОДНЯ. Так спрашивают права и
      витрина: апгрейд включает возможности сразу;
    * **с периодом** (первое число расчётного месяца) — так спрашивают ДЕНЬГИ.
      Берётся группа строки, действовавшей на первое число; если на первое число
      платной группы не было (первая покупка среди месяца), берётся текущая — это
      «появление тарифа», оно начисление как раз создаёт.

    Отсюда само собой выходит решение 14 брифа «права при апгрейде сразу, деньги
    со следующего месяца»: смена тарифа посреди месяца не переписывает уже
    названную человеку сумму, потому что расчёт смотрит на первое число.
    """
    if period is None:
        rows = (
            await db.execute(
                text(
                    "SELECT student_id, pricing_group_id FROM student_subscription "
                    " WHERE ends_on IS NULL"
                )
            )
        ).all()
    else:
        rows = (
            await db.execute(
                text(_SUBSCRIPTION_AT_PERIOD_START), {"p": period.replace(day=1)}
            )
        ).all()
    return {int(r.student_id): r.pricing_group_id for r in rows}


async def billing_group_ids(
    db: AsyncSession, *, student_id: int, period: Optional[date] = None
) -> list[int]:
    """Группы, по которым считается месяц ученика. Подписка перекрывает курсы.

    Три исхода, и их нельзя схлопнуть в два:

    * подписка с группой → **только** эта группа. Не «вдобавок к курсовой»:
      иначе ученик Self, зачисленный на курс группы «Базовый», получил бы два
      начисления за один продукт;
    * подписка без группы → пусто, начислений нет;
    * подписки нет → прежнее поведение, группы из проданных курсов.

    Пока подписки никому не присвоены (до Фазы 5), третья ветка работает для
    всех — поэтому правка ничего не меняет в деньгах до самого присвоения.

    `period` (первое число расчётного месяца) обязателен там, где считаются
    деньги: без него смена тарифа посреди месяца немедленно переписала бы уже
    открытое начисление (tsk-585).
    """
    subs = await active_subscription_groups(db, period=period)
    if student_id in subs:
        group_id = subs[student_id]
        return [int(group_id)] if group_id is not None else []

    rows = (
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
    return [int(r.group_id) for r in rows]


# ---------------------------------------------------------------- тарифные группы


async def list_groups(db: AsyncSession) -> list[PricingGroupRead]:
    rows = (
        await db.execute(
            text(
                "SELECT id, name, description, is_active FROM pricing_group "
                "ORDER BY is_active DESC, name"
            )
        )
    ).all()
    tariffs = await _load_tariffs(db)
    return [
        PricingGroupRead(
            id=r.id,
            name=r.name,
            description=r.description,
            is_active=r.is_active,
            tariffs=tariffs.get(r.id, []),
        )
        for r in rows
    ]


async def _load_tariffs(
    db: AsyncSession, *, only_active: bool = False
) -> dict[int, list[TariffRead]]:
    sql = (
        "SELECT id, group_id, name, price_minor, currency, period, match_kind, "
        "match_value, is_default, sort_order, is_active FROM pricing_tariff "
    )
    if only_active:
        sql += "WHERE is_active "
    sql += "ORDER BY group_id, sort_order, id"

    result: dict[int, list[TariffRead]] = {}
    for row in (await db.execute(text(sql))).all():
        result.setdefault(row.group_id, []).append(TariffRead.model_validate(row))
    return result


async def create_group(db: AsyncSession, *, name: str, description: Optional[str]) -> int:
    group_id = (
        await db.execute(
            text(
                "INSERT INTO pricing_group (name, description) VALUES (:name, :description) "
                "RETURNING id"
            ),
            {"name": name, "description": description},
        )
    ).scalar_one()
    await db.commit()
    return int(group_id)


async def update_group(db: AsyncSession, *, group_id: int, patch: dict) -> bool:
    """`patch` — `model_dump(exclude_unset=True)`, см. `_build_patch`."""
    sets, params = _build_patch(patch, _GROUP_PATCH_COLUMNS)
    if not sets:
        return await _exists(db, "pricing_group", group_id)
    params["id"] = group_id
    res = await db.execute(
        text(f"UPDATE pricing_group SET {sets}, updated_at = now() WHERE id = :id"), params
    )
    await db.commit()
    return res.rowcount > 0


async def delete_group(db: AsyncSession, *, group_id: int) -> bool:
    res = await db.execute(
        text("DELETE FROM pricing_group WHERE id = :id"), {"id": group_id}
    )
    await db.commit()
    return res.rowcount > 0


# ---------------------------------------------------------------- варианты тарифа


async def create_tariff(db: AsyncSession, *, payload: dict) -> int:
    tariff_id = (
        await db.execute(
            text(
                "INSERT INTO pricing_tariff "
                "(group_id, name, price_minor, currency, period, match_kind, match_value, "
                " is_default, sort_order) "
                "VALUES (:group_id, :name, :price_minor, :currency, :period, :match_kind, "
                " :match_value, :is_default, :sort_order) RETURNING id"
            ),
            payload,
        )
    ).scalar_one()
    await db.commit()
    return int(tariff_id)


async def tariff_group_id(db: AsyncSession, tariff_id: int) -> Optional[int]:
    """Группа варианта тарифа — нужна, чтобы после правки пересчитать её учеников."""
    row = (
        await db.execute(
            text("SELECT group_id FROM pricing_tariff WHERE id = :id"),
            {"id": tariff_id},
        )
    ).first()
    return int(row.group_id) if row is not None else None


async def update_tariff(db: AsyncSession, *, tariff_id: int, patch: dict) -> bool:
    """`patch` — `model_dump(exclude_unset=True)`, см. `_build_patch`."""
    sets, params = _build_patch(patch, _TARIFF_PATCH_COLUMNS)
    if not sets:
        return await _exists(db, "pricing_tariff", tariff_id)
    params["id"] = tariff_id
    res = await db.execute(
        text(f"UPDATE pricing_tariff SET {sets}, updated_at = now() WHERE id = :id"), params
    )
    await db.commit()
    return res.rowcount > 0


async def delete_tariff(db: AsyncSession, *, tariff_id: int) -> bool:
    res = await db.execute(
        text("DELETE FROM pricing_tariff WHERE id = :id"), {"id": tariff_id}
    )
    await db.commit()
    return res.rowcount > 0


# ---------------------------------------------------------------- цены курсов


async def list_course_pricing(db: AsyncSession) -> list[CoursePricingRead]:
    """Корневые курсы и их продаваемость.

    Только корневые: зачисление в LMS идёт на корень (триггеры БД), поэтому цену
    вложенному курсу назначить некому.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT c.id            AS course_id,
                       c.title,
                       c.course_uid,
                       cp.sale_status,
                       cp.group_id,
                       pg.name          AS group_name,
                       cp.note,
                       (SELECT count(*) FROM user_courses uc
                         WHERE uc.course_id = c.id AND uc.is_active) AS active_students
                  FROM courses c
                  LEFT JOIN course_parents parents ON parents.course_id = c.id
                  LEFT JOIN course_pricing cp ON cp.course_id = c.id
                  LEFT JOIN pricing_group pg ON pg.id = cp.group_id
                 WHERE parents.course_id IS NULL
                 ORDER BY (cp.sale_status = 'paid') DESC NULLS LAST, c.title
                """
            )
        )
    ).all()
    tariffs = await _load_tariffs(db, only_active=True)
    return [
        CoursePricingRead(
            course_id=r.course_id,
            title=r.title,
            course_uid=r.course_uid,
            sale_status=r.sale_status,
            group_id=r.group_id,
            group_name=r.group_name,
            note=r.note,
            tariffs=tariffs.get(r.group_id, []) if r.group_id is not None else [],
            active_students=r.active_students,
        )
        for r in rows
    ]


async def is_root_course(db: AsyncSession, course_id: int) -> bool:
    """Существует ли курс и является ли он корневым.

    Внешний ключ этого не ловит — он смотрит только на `courses.id`. Без явной
    проверки ДО записи строка вложенного курса создавалась и коммитилась, а
    ответом всё равно был 404 (список цен отдаёт только корни): пользователь
    видел ошибку, а база молча пачкалась.
    """
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM courses c "
                "LEFT JOIN course_parents p ON p.course_id = c.id "
                "WHERE c.id = :id AND p.course_id IS NULL"
            ),
            {"id": course_id},
        )
    ).first()
    return row is not None


async def set_course_pricing(
    db: AsyncSession,
    *,
    course_id: int,
    sale_status: str,
    group_id: Optional[int],
    note: Optional[str],
    updated_by: Optional[int],
) -> None:
    await db.execute(
        text(
            "INSERT INTO course_pricing (course_id, sale_status, group_id, note, updated_by) "
            "VALUES (:course_id, :sale_status, :group_id, :note, :updated_by) "
            "ON CONFLICT (course_id) DO UPDATE SET "
            "  sale_status = EXCLUDED.sale_status, "
            "  group_id = EXCLUDED.group_id, "
            "  note = EXCLUDED.note, "
            "  updated_by = EXCLUDED.updated_by, "
            "  updated_at = now()"
        ),
        {
            "course_id": course_id,
            "sale_status": sale_status,
            "group_id": group_id,
            "note": note,
            "updated_by": updated_by,
        },
    )
    await db.commit()


# ---------------------------------------------------------------- расчёт по ученикам


async def list_student_pricing(
    db: AsyncSession, *, period: Optional[date] = None
) -> list[StudentPricingRead]:
    """Расчётная цена по каждому ученику с активным зачислением на платный курс.

    Экран только для просмотра: он существует, чтобы расхождение расчёта с
    реальностью было видно глазами ДО того, как на эту модель сядут деньги.

    `period` пробрасывается в резолвер группы: без него берётся действующая
    подписка (витрина маркетолога — «как сейчас»), с ним — группа, действовавшая
    на первое число месяца (расчёт денег, tsk-585).
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id                AS student_id,
                       u.full_name,
                       cp.group_id,
                       pg.name             AS group_name,
                       c.title             AS course_title,
                       (SELECT count(*)
                              FROM lesson_slot_student lss
                              JOIN lesson_slot ls ON ls.id = lss.slot_id
                             WHERE lss.student_id = u.id
                               AND lss.is_active
                               AND ls.is_active
                               -- tsk-679: закончившийся слот в частоту не входит.
                               AND (ls.active_until IS NULL
                                    OR ls.active_until >= COALESCE(CAST(:ref AS date), CURRENT_DATE))
                               -- tsk-756: и не начавшийся тоже.
                               AND (ls.active_from IS NULL
                                    OR ls.active_from <= COALESCE(CAST(:ref AS date), CURRENT_DATE)))  AS weekly_lessons
                  FROM users u
                  JOIN user_courses uc ON uc.user_id = u.id AND uc.is_active
                  JOIN course_pricing cp ON cp.course_id = uc.course_id
                                        AND cp.sale_status = 'paid'
                  JOIN courses c ON c.id = uc.course_id
                  JOIN pricing_group pg ON pg.id = cp.group_id
                 WHERE u.is_active
                 ORDER BY u.full_name, pg.name, c.title
                """
            ),
            {"ref": reference_day(period)},
        )
    ).all()

    # tsk-301: подписка перекрывает группу курса. Строки ученика с подпиской
    # отбрасываем целиком и заменяем одной — по группе подписки. Дописывать
    # рядом нельзя: ученик Self, зачисленный на курс группы «Базовый», получил
    # бы два начисления за один продукт.
    subs = await active_subscription_groups(db, period=period)
    if subs:
        rows = [r for r in rows if r.student_id not in subs]
        # Группы берём ИЗ РЕЗОЛВЕРА, а не повторным запросом по `ends_on IS NULL`:
        # копия правила в двух местах разъезжается ровно тогда, когда правило
        # меняется — а оно только что и поменялось (tsk-585).
        paid = {sid: int(gid) for sid, gid in subs.items() if gid is not None}
        if paid:
            ids = sorted(paid)
            rows = list(rows) + list(
                (
                    await db.execute(
                        text(
                            """
                            SELECT u.id                AS student_id,
                                   u.full_name,
                                   m.group_id,
                                   pg.name             AS group_name,
                                   (SELECT string_agg(c.title, ' · ' ORDER BY c.title)
                                      FROM user_courses uc
                                      JOIN courses c ON c.id = uc.course_id
                                     WHERE uc.user_id = u.id AND uc.is_active)
                                                       AS course_title,
                                   (SELECT count(*)
                                          FROM lesson_slot_student lss
                                          JOIN lesson_slot ls ON ls.id = lss.slot_id
                                         WHERE lss.student_id = u.id
                                           AND lss.is_active
                                           AND ls.is_active
                                           -- tsk-679: закончившийся слот в частоту не входит.
                                           AND (ls.active_until IS NULL
                                                OR ls.active_until >= COALESCE(CAST(:ref AS date), CURRENT_DATE))
                                           -- tsk-756: и не начавшийся тоже.
                                           AND (ls.active_from IS NULL
                                                OR ls.active_from <= COALESCE(CAST(:ref AS date), CURRENT_DATE)))
                                                       AS weekly_lessons
                              FROM unnest(CAST(:ids AS int[]), CAST(:gids AS int[]))
                                     AS m(student_id, group_id)
                              JOIN users u ON u.id = m.student_id AND u.is_active
                              JOIN pricing_group pg ON pg.id = m.group_id
                             ORDER BY u.full_name
                            """
                        ),
                        {
                            "ids": ids,
                            "gids": [paid[i] for i in ids],
                            "ref": reference_day(period),
                        },
                    )
                ).all()
            )

    tariffs = await _load_tariffs(db, only_active=True)

    students: dict[int, StudentPricingRead] = {}
    # (student_id, group_id) → курсы этой группы у этого ученика
    buckets: dict[tuple[int, int], StudentGroupPricing] = {}

    for row in rows:
        student = students.get(row.student_id)
        if student is None:
            student = StudentPricingRead(
                student_id=row.student_id,
                full_name=row.full_name,
                weekly_lessons=row.weekly_lessons,
                groups=[],
                total_price_minor=None,
            )
            students[row.student_id] = student

        key = (row.student_id, row.group_id)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _resolve_group_price(
                group_id=row.group_id,
                group_name=row.group_name,
                weekly_lessons=row.weekly_lessons,
                tariffs=tariffs.get(row.group_id, []),
            )
            buckets[key] = bucket
            student.groups.append(bucket)
        # У подписки без зачислений курсов нет вовсе — пустое имя в список не
        # кладём, иначе на экране маркетолога появится безымянная строка.
        if row.course_title:
            bucket.course_titles.append(row.course_title)

    for student in students.values():
        prices = [g.price_minor for g in student.groups]
        # Сумма показывается только когда посчитались ВСЕ группы: частичная сумма
        # выглядела бы как полная цена и увела бы решение.
        student.total_price_minor = (
            sum(p for p in prices if p is not None) if all(p is not None for p in prices) else None
        )

    return sorted(students.values(), key=lambda s: (s.full_name or "", s.student_id))


def _resolve_group_price(
    *,
    group_id: int,
    group_name: str,
    weekly_lessons: int,
    tariffs: list[TariffRead],
) -> StudentGroupPricing:
    """Подбор варианта тарифа группы под фактическую частоту занятий ученика."""
    base = StudentGroupPricing(
        group_id=group_id,
        group_name=group_name,
        course_titles=[],
        status="no_tariff",
        tariff_id=None,
        tariff_name=None,
        price_minor=None,
    )
    if not tariffs:
        return base

    segment_options = [t for t in tariffs if t.match_kind == "segment"]
    # Нечисловое значение частоты — испорченная настройка, а не «частота 0».
    # Раньше такой тариф проходил как `(None or 0) <= weekly_lessons` и молча
    # становился ценой ученика со статусом «почти точный расчёт».
    by_frequency = [
        t
        for t in tariffs
        if t.match_kind == "attendance_frequency" and _as_int(t.match_value) is not None
    ]

    if by_frequency:
        exact = next(
            (t for t in by_frequency if _as_int(t.match_value) == weekly_lessons), None
        )
        if exact is not None:
            return _apply(base, exact, "exact")

        if weekly_lessons > 0:
            # Занятий больше, чем есть в тарифной сетке (напр. 3 при вариантах 1 и 2):
            # берём ближайший меньший и ПОМЕЧАЕМ это, а не выдаём за точное попадание.
            lower = [t for t in by_frequency if _as_int(t.match_value) <= weekly_lessons]
            if lower:
                best = max(lower, key=lambda t: _as_int(t.match_value) or 0)
                return _apply(base, best, "fallback_lower")

            # Занятий МЕНЬШЕ нижней ступени сетки. Проваливаться отсюда к
            # «единственному варианту» нельзя: он вернулся бы со статусом
            # «точное совпадение», то есть догадка выдавалась бы за расчёт.
            base.status = "below_grid"
            base.options = by_frequency
            return base

        # Расписания нет вовсе. Сегмент от расписания не зависит — если он в
        # группе есть, человеку есть что выбрать, и это не «нет расписания».
        if segment_options:
            base.status = "needs_choice"
            base.options = segment_options
            return base
        base.status = "no_schedule"
        return base

    if segment_options:
        # Сегмент выбирает человек — автоподбора здесь быть не может.
        base.status = "needs_choice"
        base.options = segment_options
        return base

    single = next((t for t in tariffs if t.match_kind is None), None)
    if single is not None:
        return _apply(base, single, "exact")

    default = next((t for t in tariffs if t.is_default), None)
    if default is not None:
        return _apply(base, default, "fallback_lower")

    base.status = "needs_choice"
    base.options = tariffs
    return base


def _apply(
    base: StudentGroupPricing, tariff: TariffRead, status: str
) -> StudentGroupPricing:
    base.status = status  # type: ignore[assignment]
    base.tariff_id = tariff.id
    base.tariff_name = tariff.name
    base.price_minor = tariff.price_minor
    return base


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("Нечисловое значение частоты в тарифе: %r", value)
        return None


# ---------------------------------------------------------------- норматив из цены (tsk-557)


@dataclass
class AttendanceFrequencyResolution:
    """Норматив занятий в неделю для дашборда посещения (tsk-557)."""

    source: FrequencySource
    #: Частота, которую нужно использовать как норматив. `None`, когда
    #: определить нечем (`source == "unknown"`).
    weekly_lessons: Optional[int]
    #: Активных слотов расписания — раздельно от `weekly_lessons`, чтобы
    #: расхождение с ценой было видно, даже когда побеждает расписание.
    schedule_weekly_lessons: int
    #: Частота, выведенная из ручной цены — раздельно, по той же причине.
    price_weekly_lessons: Optional[int]
    #: Расписание и цена разрешились, но НЕ СОВПАЛИ (прод, Юлия Сесюк 4521:
    #: 1 активный слот, а цена соответствует ступени «2 раза в неделю»).
    #: Норматив всё равно считается по расписанию — оно проверяемый факт;
    #: расхождение только помечается для методиста, не арбитруется здесь.
    discrepancy: bool


async def resolve_attendance_frequency(
    db: AsyncSession, *, student_id: int
) -> AttendanceFrequencyResolution:
    """Норматив занятий в неделю обратным выводом из цены (tsk-557).

    Расписание первично: если у ученика есть активные слоты, частота берётся
    из НИХ независимо от того, что говорит цена — расписание проверяемый
    факт, цена лишь подсказка на случай, когда расписания вовсе нет. Обратный
    проход — по ТОЙ ЖЕ тарифной сетке (`match_kind='attendance_frequency'`),
    которой `_resolve_group_price` пользуется в прямую сторону, и ТОЛЬКО при
    точном совпадении цены со ступенью. Ближайшая ступень («примерно похоже»,
    аналог `fallback_lower` в прямую сторону) здесь не годится вовсе: ученик,
    который ходит 2 раза в неделю и получил скидку, молча превратился бы в
    «1 раз в неделю» без следа. Не совпало точно — исход `unknown`: скидка/
    наценка мимо сетки, две ступени с одинаковой ценой (сама сетка
    неоднозначна) или конфликт МЕЖДУ тарифными группами одного ученика
    (у календаря нет понятия курса/группы — частота одна на всего ученика,
    выбирать между несогласными группами значило бы гадать).

    ``student_monthly_charge.manual_minor`` в вывод не участвует: это разовая
    правка суммы ОДНОГО месяца (могла отражать перерыв, частичный период —
    что угодно), а не бессрочная договорённость о частоте. Участвует только
    ``student_price_override.price_minor`` — она UNIQUE на (student, group) и
    именно она матчится с сеткой в прямую сторону (`charge_service.
    _base_price_minor`).
    """
    schedule_weekly = await _count_active_weekly_slots(db, student_id)
    price_weekly = await _infer_weekly_lessons_from_price(db, student_id)

    if schedule_weekly > 0:
        return AttendanceFrequencyResolution(
            source="schedule",
            weekly_lessons=schedule_weekly,
            schedule_weekly_lessons=schedule_weekly,
            price_weekly_lessons=price_weekly,
            discrepancy=price_weekly is not None and price_weekly != schedule_weekly,
        )

    if price_weekly is not None:
        return AttendanceFrequencyResolution(
            source="inferred_from_price",
            weekly_lessons=price_weekly,
            schedule_weekly_lessons=0,
            price_weekly_lessons=price_weekly,
            discrepancy=False,
        )

    return AttendanceFrequencyResolution(
        source="unknown",
        weekly_lessons=None,
        schedule_weekly_lessons=0,
        price_weekly_lessons=None,
        discrepancy=False,
    )


async def _count_active_weekly_slots(db: AsyncSession, student_id: int) -> int:
    """Сколько занятий в неделю у ученика по СЕГОДНЯШНЕМУ расписанию.

    tsk-679: слот с истёкшим `active_until` больше не идёт, и считать его —
    значит называть человеку частоту занятий, которой уже нет.
    """
    row = (
        await db.execute(
            text(
                "SELECT count(*) FROM lesson_slot_student lss "
                "JOIN lesson_slot ls ON ls.id = lss.slot_id "
                "WHERE lss.student_id = :s AND lss.is_active AND ls.is_active "
                "  AND (ls.active_until IS NULL OR ls.active_until >= CURRENT_DATE)"
                # tsk-756: и не начавшийся слот тоже не в счёт.
                "  AND (ls.active_from IS NULL OR ls.active_from <= CURRENT_DATE)"
            ),
            {"s": student_id},
        )
    ).scalar()
    return int(row or 0)


async def _infer_weekly_lessons_from_price(
    db: AsyncSession, student_id: int
) -> Optional[int]:
    """Обратный проход по тарифной сетке: цена → частота.

    Несколько тарифных групп у ученика — у каждой своя ручная цена и своя
    сетка. Если они выводят РАЗНЫЕ частоты — конфликт, не «выбрать одну почти
    наугад» (см. docstring `resolve_attendance_frequency`).
    """
    overrides = (
        await db.execute(
            text(
                "SELECT group_id, price_minor FROM student_price_override "
                "WHERE student_id = :s"
            ),
            {"s": student_id},
        )
    ).all()
    if not overrides:
        return None

    tariffs_by_group = await _load_tariffs(db, only_active=True)
    resolved: set[int] = set()
    for override in overrides:
        by_frequency: list[tuple[int, int]] = []
        for t in tariffs_by_group.get(override.group_id, []):
            freq = _as_int(t.match_value) if t.match_kind == "attendance_frequency" else None
            if freq is not None:
                by_frequency.append((t.price_minor, freq))
        # Ровно ОДНО совпадение цены со ступенью сетки этой группы. Ноль —
        # скидка/наценка, цена мимо сетки. Больше одного — сама сетка
        # неоднозначна (две ступени с одинаковой ценой): угадывать нельзя.
        matches = [freq for price, freq in by_frequency if price == override.price_minor]
        if len(matches) == 1:
            resolved.add(matches[0])

    if len(resolved) == 1:
        return resolved.pop()
    return None


# ---------------------------------------------------------------- вспомогательное


def _build_patch(fields: dict, allowed: frozenset[str]) -> tuple[str, dict]:
    """Собирает SET-часть UPDATE из ПРИСЛАННЫХ полей, включая `None`.

    `None` не фильтруется: иначе описание группы нельзя было бы стереть — сервер
    отвечал бы успехом, оставляя прежний текст.

    Имена колонок сверяются с белым списком. Сегодня произвольный ключ сюда не
    доедет (pydantic по умолчанию отбрасывает лишнее), но эта защита невидима в
    диффе: одна строка `ConfigDict(extra="allow")` в схеме превратила бы сборку
    SET в инъекцию.
    """
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Недопустимые поля правки: {sorted(unknown)}")
    params = dict(fields)
    sets = ", ".join(f"{k} = :{k}" for k in params)
    return sets, params


async def _exists(db: AsyncSession, table: str, row_id: int) -> bool:
    # Имя таблицы — не пользовательский ввод: вызывается с литералами из этого модуля.
    row = (
        await db.execute(text(f"SELECT 1 FROM {table} WHERE id = :id"), {"id": row_id})
    ).first()
    return row is not None
