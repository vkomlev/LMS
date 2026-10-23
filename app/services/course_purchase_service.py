"""tsk-1254 — разовая покупка курса и срок доступа к курсу.

Решения оператора 23.10.2026:

* курс можно купить целиком; доли — только через банк, способами ЮKassa. Для
  LMS это та же разовая оплата: банк перечисляет школе всю сумму сразу, долг
  ученика — перед банком. Поэтому графика долей и блокировки по ним здесь нет;
* после срока доступа курс остаётся только для чтения (как у «Выпускника»):
  материалы открыты, новые ответы не принимаются. Зачисление не удаляется;
* механизм общий для любых курсов, первым идёт пилот;
* цена и срок — в тарифе (`pricing_tariff.period = 'once'`), продление —
  кнопкой маркетолога, перевод посреди месяца — долей месяца (настройка
  `course_purchase_prorate_first_month`), возврат — вручную.

**Деньги и месяц не смешиваются.** Платёж за курс пишется в `student_payment`
с `purpose = 'course'` и без группы и месяца (правило tsk-615), поэтому ни один
месячный подсчёт его не видит и чужой месячный долг он не гасит. Сама покупка
исключает свою группу из помесячного счёта (`pricing_service.purchased_group_cutoffs`).

Рубильники (оба выключены по умолчанию): COURSE_PURCHASE_ENABLED — продажа
(картой и отметкой маркетолога), ACCESS_EXPIRY_ENABLED — проверка срока и
напоминания о его конце. Уже оплаченная покупка исключается из месячного счёта
независимо от рубильников.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)
settings = Settings()

__all__ = [
    "PURPOSE_COURSE",
    "ACCESS_EXPIRED_CODE",
    "EXPIRY_NOTICE_KIND",
    "EXPIRY_NOTICE_DAYS",
    "PurchaseError",
    "add_months",
    "sales_enabled",
    "expiry_enabled",
    "create_pending",
    "mark_paid",
    "extend_access",
    "refund",
    "list_for_student",
    "list_expiring",
    "assert_access_not_expired",
    "send_expiry_notices",
]

#: Назначение платежа за курс — то же значение, что в CHECK `student_payment`
#: и в `metadata` ЮKassa (одна метка на весь путь денег, как у пакета ИИ).
PURPOSE_COURSE = "course"

#: Код отказа «срок доступа к курсу истёк» — по нему кабинет рисует плашку.
ACCESS_EXPIRED_CODE = "course_access_expired"

#: Вид записи журнала уведомлений о скором конце доступа.
EXPIRY_NOTICE_KIND = "course_access_expiring"

#: Рубежи напоминаний, дней до конца доступа (решение 23.10: за 30 и за 7).
EXPIRY_NOTICE_DAYS: tuple[int, ...] = (30, 7)

PAY_METHODS = ("full", "bank_installments")


class PurchaseError(DomainError):
    """Покупку нельзя завести или отметить — причина в `detail`."""


def sales_enabled() -> bool:
    """Продаётся ли курс целиком. Читается на каждом вызове — для тестов и рестарта."""
    return Settings().course_purchase_enabled


def expiry_enabled() -> bool:
    """Проверяется ли срок доступа."""
    return Settings().access_expiry_enabled


def add_months(day: date, months: int) -> date:
    """Тот же день через `months` месяцев; 31.01 + 1 → 28/29.02."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def access_until_for(access_from: date, months: int) -> date:
    """Последний день доступа: год с 23.10.2026 — по 22.10.2027 включительно."""
    return add_months(access_from, months) - timedelta(days=1)


@dataclass
class _Offer:
    group_id: int
    tariff_id: int
    price_minor: int
    currency: str
    access_months: int
    installments_allowed: bool


async def _offer(db: AsyncSession, *, course_id: int, tariff_id: int) -> _Offer:
    """Разовый тариф курса: курс платный, тариф его группы, активен, `once`."""
    row = (
        await db.execute(
            text(
                """
                SELECT t.id, t.group_id, t.price_minor, t.currency, t.access_months,
                       t.installments_allowed
                  FROM course_pricing cp
                  JOIN pricing_group g ON g.id = cp.group_id AND g.is_active
                  JOIN pricing_tariff t ON t.group_id = g.id
                 WHERE cp.course_id = :c AND cp.sale_status = 'paid'
                   AND t.id = :t AND t.is_active AND t.period = 'once'
                """
            ),
            {"c": course_id, "t": tariff_id},
        )
    ).first()
    if row is None:
        raise PurchaseError(
            detail="У курса нет такого разового тарифа — проверьте цену курса у маркетолога",
            status_code=422,
        )
    return _Offer(
        group_id=int(row.group_id),
        tariff_id=int(row.id),
        price_minor=int(row.price_minor),
        currency=row.currency,
        access_months=int(row.access_months),
        installments_allowed=bool(row.installments_allowed),
    )


async def create_pending(
    db: AsyncSession,
    *,
    student_id: int,
    course_id: int,
    tariff_id: int,
    pay_method: str = "full",
    created_by: Optional[int] = None,
    note: Optional[str] = None,
) -> dict:
    """Завести покупку в статусе «ждёт оплаты». Цена и срок — снимком тарифа.

    Ученик должен быть зачислен на курс: срок доступа хранится на зачислении,
    и покупка без него не открыла бы ничего. Зачислять здесь же не берёмся —
    у зачисления свои триггеры и правила графа курсов.

    Не коммитит — коммит за вызывающим (создание платежа в шлюзе идёт следом).
    """
    if not sales_enabled():
        raise PurchaseError(detail="Покупка курса целиком пока не включена", status_code=403)
    if pay_method not in PAY_METHODS:
        raise PurchaseError(detail="Неизвестный способ оплаты", status_code=422)
    offer = await _offer(db, course_id=course_id, tariff_id=tariff_id)
    if pay_method == "bank_installments" and not offer.installments_allowed:
        raise PurchaseError(detail="По этому тарифу оплата долями недоступна", status_code=422)
    enrolled = (
        await db.execute(
            text("SELECT 1 FROM user_courses WHERE user_id = :s AND course_id = :c"),
            {"s": student_id, "c": course_id},
        )
    ).first()
    if enrolled is None:
        raise PurchaseError(
            detail="Ученик не зачислен на этот курс — сначала зачислите его",
            status_code=409,
        )
    # Помесячный счёт снимается с тарифной ГРУППЫ целиком. Если в той же группе
    # у ученика есть другой действующий курс, покупка одного молча сделала бы
    # бесплатным и второй — такую покупку не заводим, решает маркетолог.
    sibling = (
        await db.execute(
            text(
                "SELECT c.title FROM user_courses uc "
                "  JOIN course_pricing cp ON cp.course_id = uc.course_id "
                "                        AND cp.sale_status = 'paid' AND cp.group_id = :g "
                "  JOIN courses c ON c.id = uc.course_id "
                " WHERE uc.user_id = :s AND uc.is_active AND uc.course_id <> :c LIMIT 1"
            ),
            {"s": student_id, "g": offer.group_id, "c": course_id},
        )
    ).first()
    if sibling is not None:
        raise PurchaseError(
            detail=(
                f"В той же тарифной группе у ученика есть курс «{sibling.title}» — "
                "покупка одного курса сняла бы помесячную оплату и за него"
            ),
            status_code=409,
        )
    # Второй клик по «Оплатить» не заводит вторую покупку: действующая
    # оплаченная — отказ, ждущая с тем же тарифом — та же самая (и тот же ключ
    # повтора в шлюзе, то есть тот же платёж ЮKassa).
    current = (
        await db.execute(
            text(
                "SELECT id, status, tariff_id, pay_method, price_minor, access_months "
                "  FROM course_purchase "
                " WHERE student_id = :s AND course_id = :c "
                "   AND (status = 'pending' OR (status = 'paid' AND access_until >= CURRENT_DATE)) "
                " ORDER BY (status = 'paid') DESC, id DESC LIMIT 1"
            ),
            {"s": student_id, "c": course_id},
        )
    ).first()
    if current is not None and current.status == "paid":
        raise PurchaseError(detail="Курс уже оплачен, доступ действует", status_code=409)
    if (
        current is not None
        and current.tariff_id == offer.tariff_id
        and current.pay_method == pay_method
        and current.price_minor == offer.price_minor
    ):
        return {
            "id": int(current.id),
            "price_minor": int(current.price_minor),
            "access_months": int(current.access_months),
            "pay_method": pay_method,
        }
    row = (
        await db.execute(
            text(
                """
                INSERT INTO course_purchase
                       (student_id, course_id, group_id, tariff_id, price_minor, currency,
                        access_months, pay_method, created_by, note)
                VALUES (:s, :c, :g, :t, :price, :cur, :months, :method, :by, :note)
                RETURNING id
                """
            ),
            {
                "s": student_id,
                "c": course_id,
                "g": offer.group_id,
                "t": offer.tariff_id,
                "price": offer.price_minor,
                "cur": offer.currency,
                "months": offer.access_months,
                "method": pay_method,
                "by": created_by,
                "note": note,
            },
        )
    ).first()
    return {
        "id": int(row.id),
        "price_minor": offer.price_minor,
        "access_months": offer.access_months,
        "pay_method": pay_method,
    }


async def mark_paid(
    db: AsyncSession,
    *,
    purchase_id: int,
    amount_minor: int,
    paid_on: date,
    gateway_payment_id: Optional[str] = None,
    staff_id: Optional[int] = None,
) -> bool:
    """Зачесть оплату покупки. `True` — зачли сейчас, `False` — уже была оплачена.

    Одной транзакцией: статус покупки, срок доступа, платёж и срок на
    зачислении. Частичный исход («деньги записаны, доступ не выдан») хуже
    отказа — поэтому при любой ошибке откат целиком.

    Сумма должна совпасть с ценой-снимком: доли через банк приходят школе
    всей суммой сразу, а недоплату через шлюз мы не заводим. Расхождение —
    повод разобраться человеку, а не молча выдать год доступа.

    После коммита пересчитывается текущий месяц ученика: открытая строка
    помесячного начисления по купленной группе обнуляется или режется долей.
    """
    try:
        row = (
            await db.execute(
                text(
                    "SELECT id, student_id, course_id, group_id, price_minor, access_months, "
                    "       status "
                    "  FROM course_purchase WHERE id = :id FOR UPDATE"
                ),
                {"id": purchase_id},
            )
        ).first()
        if row is None:
            raise PurchaseError(detail="Покупка не найдена", status_code=404)
        if row.status == "paid":
            await db.rollback()
            logger.info("tsk-1254: покупка %s уже оплачена — повтор пропущен", purchase_id)
            # Пересчёт всё равно: прошлый раз он мог упасть после коммита, и
            # открытая месячная строка по купленной группе держала бы блокировку.
            from app.services import charge_service

            await charge_service.recalculate_for_student(db, student_id=int(row.student_id))
            return False
        if row.status != "pending":
            raise PurchaseError(
                detail=f"Покупка в статусе «{row.status}» — оплату не принять",
                status_code=409,
            )
        if amount_minor != row.price_minor:
            raise PurchaseError(
                detail=(
                    f"Сумма {amount_minor} коп. не равна цене покупки "
                    f"{row.price_minor} коп."
                ),
                status_code=422,
            )

        until = access_until_for(paid_on, int(row.access_months))
        await db.execute(
            text(
                "UPDATE course_purchase SET status = 'paid', access_from = :f, "
                "       access_until = :u, updated_at = now() WHERE id = :id"
            ),
            {"id": purchase_id, "f": paid_on, "u": until},
        )
        if gateway_payment_id is not None:
            await db.execute(
                text(
                    """
                    INSERT INTO student_payment
                           (student_id, amount_minor, method, gateway, gateway_payment_id,
                            paid_on, purpose, course_purchase_id, status, reviewed_at,
                            review_note)
                    VALUES (:s, :amt, 'gateway', 'yookassa', :txn, :paid_on, 'course', :pid,
                            'confirmed', now(), 'Оплата курса картой, подтверждена шлюзом')
                    """
                ),
                {
                    "s": row.student_id,
                    "amt": amount_minor,
                    "txn": gateway_payment_id,
                    "paid_on": paid_on,
                    "pid": purchase_id,
                },
            )
        else:
            await db.execute(
                text(
                    """
                    INSERT INTO student_payment
                           (student_id, amount_minor, method, paid_on, purpose,
                            course_purchase_id, status, submitted_by, reviewed_by,
                            reviewed_at, review_note)
                    VALUES (:s, :amt, 'manual', :paid_on, 'course', :pid,
                            'confirmed', :by, :by, now(), 'Оплата курса отмечена маркетологом')
                    """
                ),
                {
                    "s": row.student_id,
                    "amt": amount_minor,
                    "paid_on": paid_on,
                    "pid": purchase_id,
                    "by": staff_id,
                },
            )
        # Продление поверх действующего срока не укорачивает его: берём больший.
        await db.execute(
            text(
                "UPDATE user_courses SET access_until = GREATEST(COALESCE(access_until, :u), :u) "
                " WHERE user_id = :s AND course_id = :c"
            ),
            {"s": row.student_id, "c": row.course_id, "u": until},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info(
        "tsk-1254: покупка %s оплачена — ученик %s, курс %s, доступ %s..%s",
        purchase_id, row.student_id, row.course_id, paid_on, until,
    )
    from app.services import charge_service

    await charge_service.recalculate_for_student(
        db, student_id=int(row.student_id), period=paid_on
    )
    return True


async def refund(
    db: AsyncSession, *, purchase_id: int, staff_id: Optional[int], note: Optional[str]
) -> bool:
    """Отметить возврат покупки (деньги возвращены вручную, решение 23.10).

    Покупка → `refunded`: она перестаёт снимать группу с помесячного счёта.
    Её платёж → `reversed` с пометкой, чтобы выручка не считалась дважды.
    Доступ закрывается сегодняшним днём (только чтение), а не удаляется.
    Возвращает False, если покупка не оплачена.
    """
    row = (
        await db.execute(
            text(
                "SELECT student_id, course_id, status FROM course_purchase "
                " WHERE id = :id FOR UPDATE"
            ),
            {"id": purchase_id},
        )
    ).first()
    if row is None or row.status != "paid":
        await db.rollback()
        return False
    await db.execute(
        text(
            "UPDATE course_purchase SET status = 'refunded', updated_at = now(), "
            "       note = concat_ws(' | ', note, CAST(:note AS text)) WHERE id = :id"
        ),
        {"id": purchase_id, "note": note},
    )
    await db.execute(
        text(
            "UPDATE student_payment SET status = 'reversed', reviewed_by = :by, "
            "       reviewed_at = now(), updated_at = now(), "
            "       review_note = concat_ws(' | ', review_note, 'Возврат за курс') "
            " WHERE course_purchase_id = :id AND status = 'confirmed'"
        ),
        {"id": purchase_id, "by": staff_id},
    )
    await db.execute(
        text(
            "UPDATE user_courses SET access_until = LEAST(COALESCE(access_until, CURRENT_DATE), "
            "       CURRENT_DATE) WHERE user_id = :s AND course_id = :c"
        ),
        {"s": row.student_id, "c": row.course_id},
    )
    await db.commit()
    logger.info("tsk-1254: покупка %s — возврат отмечен", purchase_id)
    from app.services import charge_service

    await charge_service.recalculate_for_student(db, student_id=int(row.student_id))
    return True


async def extend_access(
    db: AsyncSession, *, student_id: int, course_id: int, months: int
) -> Optional[date]:
    """Продлить доступ к курсу на `months` месяцев (кнопка маркетолога).

    От большего из «сегодня» и текущего конца: продление истёкшего доступа
    отсчитывается от сегодня, а не от прошлого. Бессрочный доступ (NULL) не
    трогается — продлевать его некуда. Возвращает новый срок либо None, если
    зачисления нет или доступ бессрочный.
    """
    if not 1 <= months <= 120:
        raise PurchaseError(detail="Продлить можно на 1–120 месяцев", status_code=422)
    row = (
        await db.execute(
            text(
                "SELECT access_until FROM user_courses "
                " WHERE user_id = :s AND course_id = :c FOR UPDATE"
            ),
            {"s": student_id, "c": course_id},
        )
    ).first()
    if row is None or row.access_until is None:
        await db.rollback()
        return None
    # «N месяцев со дня, следующего за концом», с тем же «−1 день», что у
    # покупки: продление на год срока по 22.10 даёт срок по 22.10 следующего года.
    start = max(row.access_until, date.today() - timedelta(days=1)) + timedelta(days=1)
    new_until = access_until_for(start, months)
    await db.execute(
        text("UPDATE user_courses SET access_until = :u WHERE user_id = :s AND course_id = :c"),
        {"s": student_id, "c": course_id, "u": new_until},
    )
    await db.commit()
    logger.info(
        "tsk-1254: доступ ученика %s к курсу %s продлён до %s", student_id, course_id, new_until
    )
    return new_until


async def list_for_student(db: AsyncSession, *, student_id: int) -> list[dict]:
    """Покупки ученика со сроком доступа на зачислении — для кабинета и карточки."""
    rows = (
        await db.execute(
            text(
                """
                SELECT cp.id, cp.course_id, c.title AS course_title, cp.price_minor,
                       cp.currency, cp.access_months, cp.pay_method, cp.status,
                       cp.access_from, cp.access_until AS purchase_access_until,
                       uc.access_until, cp.created_at
                  FROM course_purchase cp
                  JOIN courses c ON c.id = cp.course_id
                  LEFT JOIN user_courses uc ON uc.user_id = cp.student_id
                                           AND uc.course_id = cp.course_id
                 WHERE cp.student_id = :s
                 ORDER BY cp.created_at DESC, cp.id DESC
                """
            ),
            {"s": student_id},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def list_expiring(
    db: AsyncSession, *, within_days: int = 30, today: Optional[date] = None
) -> list[dict]:
    """Зачисления, чей доступ кончается в ближайшие `within_days` дней или уже кончился за месяц."""
    today = today or date.today()
    rows = (
        await db.execute(
            text(
                """
                SELECT uc.user_id AS student_id, u.full_name, u.email, uc.course_id,
                       c.title AS course_title, uc.access_until
                  FROM user_courses uc
                  JOIN users u ON u.id = uc.user_id AND u.is_active
                  JOIN courses c ON c.id = uc.course_id
                 WHERE uc.access_until IS NOT NULL
                   AND uc.access_until BETWEEN CAST(:from_d AS date) AND CAST(:to_d AS date)
                 ORDER BY uc.access_until, u.full_name
                """
            ),
            {"from_d": today - timedelta(days=30), "to_d": today + timedelta(days=within_days)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


#: Зачисление ученика на курс или любого его предка с истёкшим сроком. Задания
#: живут в подкурсах, а срок — на корневом зачислении, поэтому идём вверх.
_EXPIRED_FOR_COURSE = """
WITH RECURSIVE up AS (
    SELECT CAST(:c AS integer) AS course_id, 0 AS depth
    UNION
    SELECT cp.parent_course_id, up.depth + 1
      FROM course_parents cp JOIN up ON cp.course_id = up.course_id
     WHERE up.depth < 20
)
SELECT uc.course_id, uc.access_until
  FROM user_courses uc
 WHERE uc.user_id = :s
   AND uc.course_id IN (SELECT course_id FROM up)
   AND uc.access_until IS NOT NULL
   AND uc.access_until < CAST(:today AS date)
   -- Подкурс бывает общим у двух корней: пока хоть одно зачисление на пути
   -- живо (бессрочно или срок не кончился), сдача открыта.
   AND NOT EXISTS (
           SELECT 1 FROM user_courses live
            WHERE live.user_id = :s AND live.is_active
              AND live.course_id IN (SELECT course_id FROM up)
              AND (live.access_until IS NULL OR live.access_until >= CAST(:today AS date))
       )
 LIMIT 1
"""


async def assert_access_not_expired(
    db: AsyncSession, *, student_id: int, course_id: Optional[int]
) -> None:
    """Закрыть сдачу ответов, если срок доступа к курсу истёк (только чтение).

    Работает лишь при ACCESS_EXPIRY_ENABLED и известном курсе. Без курса
    пропускаем: гадать, к какому курсу относится попытка, значило бы закрыть
    чужой курс того же ученика.
    """
    if not expiry_enabled() or course_id is None:
        return
    row = (
        await db.execute(
            text(_EXPIRED_FOR_COURSE),
            {"s": student_id, "c": course_id, "today": date.today()},
        )
    ).first()
    if row is None:
        return
    logger.info(
        "tsk-1254: сдача закрыта ученику %s — доступ к курсу %s кончился %s",
        student_id, row.course_id, row.access_until,
    )
    raise DomainError(
        detail=(
            f"Срок доступа к курсу закончился {row.access_until:%d.%m.%Y}. Материалы "
            "остаются открытыми для чтения, новые ответы не принимаются."
        ),
        status_code=403,
        payload={"code": ACCESS_EXPIRED_CODE, "access_until": row.access_until.isoformat()},
    )


async def send_expiry_notices(db: AsyncSession, *, today: Optional[date] = None) -> int:
    """Напомнить о скором конце доступа: за 30 и за 7 дней, раз на рубеж.

    Кабинет (журнал `notifications`, оттуда — дубль в мессенджеры) и письмо.
    Рубеж ловится окном «осталось не больше N дней и ещё не напоминали на этом
    рубеже для этого срока», а не точным днём: пропущенный проход крона иначе
    терял бы напоминание навсегда. Новый срок после продления — новые рубежи.
    Возвращает число отправленных напоминаний.
    """
    if not expiry_enabled():
        return 0
    from app.services import inbox_service, notification_email_service

    today = today or date.today()
    outbox: list[dict] = []
    for item in await list_expiring(db, within_days=max(EXPIRY_NOTICE_DAYS), today=today):
        until: date = item["access_until"]
        left = (until - today).days
        if left < 0:
            continue
        threshold = min(d for d in EXPIRY_NOTICE_DAYS if d >= left)
        already = (
            await db.execute(
                text(
                    "SELECT 1 FROM notifications WHERE user_id = :u AND kind = :k "
                    "   AND payload->>'course_id' = :c AND payload->>'access_until' = :until "
                    "   AND (payload->>'threshold')::int <= :th LIMIT 1"
                ),
                {
                    "u": item["student_id"],
                    "k": EXPIRY_NOTICE_KIND,
                    "c": str(item["course_id"]),
                    "until": until.isoformat(),
                    "th": threshold,
                },
            )
        ).first()
        if already is not None:
            continue
        await inbox_service.create_for_user(
            db,
            user_id=item["student_id"],
            kind=EXPIRY_NOTICE_KIND,
            title="Скоро закончится доступ к курсу",
            content=(
                f"Доступ к курсу «{item['course_title']}» открыт по {until:%d.%m.%Y}. "
                "После этого материалы останутся для чтения, а ответы приниматься не будут."
            ),
            payload={
                "course_id": item["course_id"],
                "access_until": until.isoformat(),
                "threshold": threshold,
            },
            created_by=None,
        )
        outbox.append(item)
    # Один коммит на проход: вызывающий держит транзакционный advisory-lock,
    # а коммит посреди цикла снял бы его, и второй worker разослал бы то же.
    await db.commit()
    # Письма — после коммита: сбой почты не должен откатывать запись в кабинете.
    for item in outbox:
        if item.get("email"):
            await notification_email_service.send_course_access_expiring(
                recipient_email=item["email"],
                full_name=item["full_name"],
                course_title=item["course_title"],
                access_until=item["access_until"],
                settings=settings,
            )
    sent = len(outbox)
    if sent:
        logger.info("tsk-1254: напоминаний о конце доступа отправлено: %s", sent)
    return sent
