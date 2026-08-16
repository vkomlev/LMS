"""tsk-010 — приём оплаты: факт денег поверх уже посчитанного начисления.

Здесь нет ответа на вопрос «сколько должен ученик» — на него отвечает
`charge_service` (`student_monthly_charge`). Этот модуль отвечает только на
вопрос «сколько из этого пришло и чем подтверждено».

Оплаченность не хранится полем: она каждый раз выводится суммой подтверждённых
платежей против итога начисления. Так частичная оплата, правка суммы месяца и
перенос с прошлого месяца не разъезжаются между двумя источниками правды.

tsk-615: не всякий платёж относится к месяцу. Разовая покупка (пакет обращений
к наставнику и всё, что дальше продадим не за месяц) живёт в этой же таблице,
но с `purpose <> 'monthly'` и пустыми `group_id`/`period`. Разные таблицы под
разные продажи разъехались бы: сверку со шлюзом, выгрузку для «Мой налог» и
кабинет пришлось бы держать в двух местах, а номер платежа ЮKassa перестал бы
быть уникальным на все деньги сразу.

Отсюда правило для всех подсчётов месяца: они фильтруют по `period`/`group_id`,
поэтому разовая покупка в них не попадает сама собой — и не гасит чужой долг.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import charge_service

logger = logging.getLogger(__name__)
settings = Settings()

__all__ = [
    "PaymentStatus",
    "PaymentPurpose",
    "PURPOSE_AI_PACKAGE",
    "PURPOSE_NAMES",
    "DuplicatePaymentError",
    "ChargePaymentState",
    "due_date_for",
    "block_date_for",
    "payment_state",
    "attach_payment_state",
    "charge_for_student",
    "list_student_charges",
    "list_student_purchases",
    "create_manual_payment",
    "record_gateway_payment",
    "record_staff_payment",
    "charge_by_id",
    "list_payments",
    "confirm_payment",
    "reject_payment",
    "get_receipt",
    "export_confirmed",
    "student_ids_for_parent",
]

PaymentStatus = Literal["pending", "confirmed", "rejected"]

#: За что заплатили. Список закрыт и продублирован в CHECK таблицы: новая
#: разовая продажа добавляется сюда И миграцией — чтобы описка не создала класс
#: денег, который не показывается нигде (tsk-615).
PaymentPurpose = Literal["monthly", "ai_package"]

#: Разовая покупка пакета обращений. Тем же значением помечается платёж в
#: `metadata` ЮKassa — одна метка на оба конца: по ней и приём уведомления, и
#: сверка понимают, что начисления у этих денег нет и искать его не нужно.
PURPOSE_AI_PACKAGE = "ai_package"

#: Назначение человеку — в кабинет ученика и в выгрузку. Иначе в списке
#: покупок стоял бы машинный код.
PURPOSE_NAMES: dict[str, str] = {
    "monthly": "Обучение за месяц",
    "ai_package": "Пакет обращений к ИИ-наставнику",
}


class DuplicatePaymentError(RuntimeError):
    """Такой же чек уже отправлен и ещё не разобран."""

#: Итог по начислению: сколько подтверждено, сколько ждёт решения, что осталось.
@dataclass
class ChargePaymentState:
    paid_minor: int
    pending_minor: int
    due_minor: int
    #: Пришло больше, чем начислено. Отдельным числом, потому что «долг = 0»
    #: одинаково выглядит и при точной оплате, и при переплате — а разница в
    #: деньгах человека.
    overpaid_minor: int
    #: Месяц закончился, деньги не пришли — пометка в кабинете и письмо.
    is_overdue: bool
    #: Просрочка затянулась настолько, что закрываются занятия. Отдельно от
    #: `is_overdue`: между ними несколько дней, за которые человек может успеть
    #: заплатить, не потеряв доступ.
    is_blocked: bool


def due_date_for(period: date) -> date:
    """Крайний день оплаты месяца — его последний день.

    За август платят до 31 августа: цикл школы такой, что месяц оплачивается до
    своего конца, а должником человек становится уже в следующем месяце.
    """
    return charge_service.next_month(period) - timedelta(days=1)


def block_date_for(period: date) -> date:
    """День, с которого неоплата закрывает занятия.

    Отдельно от `due_date_for`: пометка «просрочено» и письмо появляются сразу
    после конца месяца, а доступ закрывается на несколько дней позже — человеку
    нужно время заплатить после того, как месяц закончился.
    """
    return due_date_for(period) + timedelta(days=settings.payment_block_after_days)


def payment_state(
    *, total_minor: int, paid_minor: int, pending_minor: int, period: date, today: date
) -> ChargePaymentState:
    """Состояние оплаты одного начисления.

    Просрочка — это только про деньги, которых ещё нет: платёж, ждущий решения
    маркетолога, просрочкой не считается, иначе ученик, честно приложивший чек
    первого числа, оказался бы должником из-за нашей очереди. Но гасит просрочку
    только чек, ПОКРЫВАЮЩИЙ остаток: иначе приложенный рубль снимал бы признак
    просрочки с любого долга.

    Просрочка начинается ПОСЛЕ конца оплачиваемого месяца: пока месяц идёт,
    человек не должник, даже если ещё не заплатил.
    """
    due = max(total_minor - paid_minor, 0)
    unpaid = due > 0 and pending_minor < due
    overdue = unpaid and today > due_date_for(period)
    return ChargePaymentState(
        paid_minor=paid_minor,
        pending_minor=pending_minor,
        due_minor=due,
        overpaid_minor=max(paid_minor - total_minor, 0),
        is_overdue=overdue,
        is_blocked=unpaid and today >= block_date_for(period),
    )


async def _totals_by_charge(
    db: AsyncSession, *, period: Optional[date] = None, student_id: Optional[int] = None
) -> dict[tuple[int, int, date], tuple[int, int]]:
    """Подтверждено и ждёт решения — по каждому начислению разом.

    Одним запросом на весь список: иначе экран начислений дал бы запрос на
    строку и разъехался бы по времени между строками.

    Разовые покупки сюда не идут: у них нет месяца, и деньги за пакет не должны
    гасить долг за обучение. Фильтр по `purpose` стоит явно, а не полагается на
    то, что NULL-месяц не сойдётся с ключом группировки, — иначе связь была бы
    случайной и первая же строка с частично заполненным месяцем всё сломала бы.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT student_id, group_id, period,
                       COALESCE(sum(amount_minor) FILTER (WHERE status = 'confirmed'), 0) AS paid,
                       COALESCE(sum(amount_minor) FILTER (WHERE status = 'pending'), 0) AS pending
                  FROM student_payment
                 -- CAST на параметре: у необязательного фильтра asyncpg иначе
                 -- не может вывести тип NULL и роняет запрос целиком.
                 WHERE purpose = 'monthly'
                   AND (CAST(:p AS date) IS NULL OR period = CAST(:p AS date))
                   AND (CAST(:s AS integer) IS NULL OR student_id = CAST(:s AS integer))
                 GROUP BY student_id, group_id, period
                """
            ),
            {"p": period, "s": student_id},
        )
    ).all()
    return {
        (r.student_id, r.group_id, r.period): (int(r.paid), int(r.pending)) for r in rows
    }


async def attach_payment_state(
    db: AsyncSession, charges: list[dict], *, period: Optional[date] = None
) -> list[dict]:
    """Дописать к строкам начислений, сколько по ним уже пришло.

    Работает поверх готового результата `charge_service.list_charges`, чтобы
    расчёт остался единственным местом, где складывается сумма месяца.
    """
    totals = await _totals_by_charge(db, period=period)
    today = date.today()
    for row in charges:
        key = (row["student_id"], row["group_id"], row["period"])
        paid, pending = totals.get(key, (0, 0))
        state = payment_state(
            total_minor=row["total_minor"],
            paid_minor=paid,
            pending_minor=pending,
            period=row["period"],
            today=today,
        )
        row["paid_minor"] = state.paid_minor
        row["pending_minor"] = state.pending_minor
        row["due_minor"] = state.due_minor
        row["overpaid_minor"] = state.overpaid_minor
        row["is_overdue"] = state.is_overdue
    return charges


async def charge_for_student(
    db: AsyncSession, *, charge_id: int, student_id: int
) -> Optional[dict]:
    """Начисление ученика по id — с проверкой, что оно действительно его.

    Проверка принадлежности живёт в самом запросе: платёж по чужому id не должен
    отличаться от платежа по несуществующему, иначе перебором номеров всплывут
    чужие месяцы и суммы.
    """
    row = (
        await db.execute(
            text(
                "SELECT id, student_id, group_id, period, status "
                "  FROM student_monthly_charge "
                " WHERE id = :id AND student_id = :s"
            ),
            {"id": charge_id, "s": student_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def list_student_charges(db: AsyncSession, *, student_id: int) -> list[dict]:
    """Начисления одного ученика для его кабинета — с историей платежей.

    Отдаём и закрытые месяцы: ученик должен видеть, что за прошлый месяц долг
    погашен, а не только текущую строку.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT ch.id,
                       ch.group_id,
                       pg.name AS group_name,
                       ch.period,
                       ch.calculated_minor,
                       ch.manual_minor,
                       ch.status,
                       COALESCE(adj.total, 0) AS adjustments_minor
                  FROM student_monthly_charge ch
                  JOIN pricing_group pg ON pg.id = ch.group_id
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total
                          FROM charge_adjustment a
                         WHERE a.student_id = ch.student_id
                           AND a.group_id = ch.group_id
                           AND a.period = ch.period
                  ) adj ON TRUE
                 WHERE ch.student_id = :s
                 ORDER BY ch.period DESC, pg.name
                """
            ),
            {"s": student_id},
        )
    ).all()

    payments = await _payments_by_charge(db, student_id=student_id)
    totals = await _totals_by_charge(db, student_id=student_id)
    today = date.today()

    result: list[dict] = []
    for r in rows:
        total = charge_service.charge_total_minor(
            calculated_minor=r.calculated_minor,
            manual_minor=r.manual_minor,
            adjustments_minor=int(r.adjustments_minor),
        )
        paid, pending = totals.get((student_id, r.group_id, r.period), (0, 0))
        state = payment_state(
            total_minor=total,
            paid_minor=paid,
            pending_minor=pending,
            period=r.period,
            today=today,
        )
        result.append(
            {
                "id": r.id,
                "group_id": r.group_id,
                "group_name": r.group_name,
                "period": r.period,
                "total_minor": total,
                "status": r.status,
                "due_on": due_date_for(r.period),
                "paid_minor": state.paid_minor,
                "pending_minor": state.pending_minor,
                "due_minor": state.due_minor,
                "overpaid_minor": state.overpaid_minor,
                "is_overdue": state.is_overdue,
                "payments": payments.get((r.group_id, r.period), []),
            }
        )
    return result


async def _payments_by_charge(
    db: AsyncSession, *, student_id: int
) -> dict[tuple[int, date], list[dict]]:
    """История платежей ЗА МЕСЯЦЫ, разложенная по месяцам и группам.

    Разовые покупки живут в той же таблице, но месяца у них нет — их отдаёт
    `list_student_purchases` отдельным списком.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT id, group_id, period, amount_minor, method, status,
                       receipt_name, payer_note, paid_on, review_note,
                       reviewed_at, created_at
                  FROM student_payment
                 WHERE student_id = :s AND purpose = 'monthly'
                 ORDER BY created_at DESC
                """
            ),
            {"s": student_id},
        )
    ).all()
    grouped: dict[tuple[int, date], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r.group_id, r.period), []).append(
            {
                "id": r.id,
                "amount_minor": r.amount_minor,
                "method": r.method,
                "status": r.status,
                "receipt_name": r.receipt_name,
                "payer_note": r.payer_note,
                "paid_on": r.paid_on,
                "review_note": r.review_note,
                "reviewed_at": r.reviewed_at,
                "created_at": r.created_at,
            }
        )
    return grouped


async def list_student_purchases(db: AsyncSession, *, student_id: int) -> list[dict]:
    """Разовые покупки ученика — то, что оплачено не за месяц (tsk-615).

    Отдельный список, а не строки внутри месяцев: у покупки нет месяца, и
    приписать её к какому-нибудь ближайшему значило бы соврать в обе стороны —
    и в истории покупок, и в оплаченности того месяца.

    Отклонённые не прячем: человек, отправивший деньги, должен видеть, что с
    ними стало, иначе покупка выглядит пропавшей.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT id, purpose, amount_minor, method, status,
                       payer_note, paid_on, review_note, reviewed_at, created_at
                  FROM student_payment
                 WHERE student_id = :s AND purpose <> 'monthly'
                 ORDER BY COALESCE(paid_on, created_at::date) DESC, id DESC
                """
            ),
            {"s": student_id},
        )
    ).all()
    return [
        {**dict(r._mapping), "purpose_name": PURPOSE_NAMES.get(r.purpose, r.purpose)}
        for r in rows
    ]


async def create_manual_payment(
    db: AsyncSession,
    *,
    student_id: int,
    group_id: int,
    period: date,
    amount_minor: int,
    paid_on: Optional[date],
    payer_note: Optional[str],
    receipt_file: Optional[str],
    receipt_name: Optional[str],
    submitted_by: int,
) -> int:
    """Завести платёж, ждущий подтверждения. Возвращает его id.

    Сумму не сверяем с начислением: ученик мог заплатить часть, а мог и с
    запасом. Расхождение — повод для решения маркетолога, а не для отказа на
    входе; отказ здесь просто оставил бы деньги вне системы.

    `DuplicatePaymentError` — второй такой же чек, ещё не разобранный. Это почти
    всегда потерянный ответ и повторное нажатие; пропустить его значит завести
    вторые деньги, которые маркетолог подтвердит, не имея повода усомниться.

    Оплату ЗАКРЫТОГО месяца принимаем сознательно. Закрытие замораживает сумму
    месяца, а не запрещает гасить долг: иначе задолженность за уже закрытый июль
    погасить было бы нечем.
    """
    try:
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO student_payment
                           (student_id, group_id, period, amount_minor, method,
                            receipt_file, receipt_name, payer_note, paid_on, submitted_by)
                    VALUES (:s, :g, :p, :amt, 'manual', :file, :name, :note, :paid_on, :by)
                    RETURNING id
                    """
                ),
                {
                    "s": student_id,
                    "g": group_id,
                    "p": period,
                    "amt": amount_minor,
                    "file": receipt_file,
                    "name": receipt_name,
                    "note": payer_note,
                    "paid_on": paid_on,
                    "by": submitted_by,
                },
            )
        ).one()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_student_payment_pending_duplicate" in str(exc.orig):
            raise DuplicatePaymentError from exc
        raise
    await db.commit()
    logger.info(
        "tsk-010: платёж %s заведён ученику %s за %s на %s коп. (загрузил %s)",
        row.id,
        student_id,
        period,
        amount_minor,
        submitted_by,
    )
    return int(row.id)


async def record_gateway_payment(
    db: AsyncSession,
    *,
    student_id: int,
    group_id: Optional[int],
    period: Optional[date],
    amount_minor: int,
    gateway: str,
    gateway_payment_id: str,
    paid_on: Optional[date],
    purpose: PaymentPurpose = "monthly",
    review_note: str = "Оплата картой, подтверждена шлюзом",
) -> bool:
    """Зачесть платёж, пришедший от шлюза. `True` — записали, `False` — уже был.

    Сразу `confirmed`: деньги на счёте, подтверждать маркетологу нечего. Автор
    решения — не человек, поэтому `reviewed_by` пуст, а `reviewed_at` заполнен
    (без него строка не пройдёт проверку «решение всегда со следом»).

    Идемпотентность держится на уникальном индексе по паре «шлюз + номер
    транзакции», а не на проверке в коде: повторная доставка уведомления —
    обычное дело, и две одновременные доставки не должны разойтись в гонке.

    tsk-615: у разовой покупки (`purpose <> 'monthly'`) месяца и группы нет —
    передаются пустыми. Пару «назначение ↔ месяц» держит CHECK таблицы, поэтому
    перепутать здесь нельзя молча: месячный платёж без месяца не запишется.

    **CAST на месяце и группе обязателен**: у разовой покупки оба параметра
    приходят пустыми, и без явного типа asyncpg не выводит тип NULL — запрос
    падает целиком (та же гоча, что у необязательных фильтров выше).
    """
    res = await db.execute(
        text(
            """
            INSERT INTO student_payment
                   (student_id, group_id, period, amount_minor, method,
                    gateway, gateway_payment_id, paid_on, purpose,
                    status, reviewed_at, review_note)
            VALUES (:s, CAST(:g AS integer), CAST(:p AS date), :amt, 'gateway',
                    :gw, :txn, :paid_on, :purpose,
                    'confirmed', now(), :note)
            ON CONFLICT (gateway, gateway_payment_id)
                WHERE gateway_payment_id IS NOT NULL
            DO NOTHING
            RETURNING id
            """
        ),
        {
            "s": student_id,
            "g": group_id,
            "p": period,
            "amt": amount_minor,
            "gw": gateway,
            "txn": gateway_payment_id,
            "paid_on": paid_on,
            "purpose": purpose,
            "note": review_note,
        },
    )
    row = res.first()
    await db.commit()
    if row is None:
        logger.info(
            "tsk-010: повторное уведомление по транзакции %s/%s — платёж уже учтён",
            gateway,
            gateway_payment_id,
        )
        return False
    logger.info(
        "tsk-010: платёж картой %s зачтён ученику %s (%s, %s) на %s коп. (транзакция %s)",
        row.id,
        student_id,
        purpose,
        period or "без месяца",
        amount_minor,
        gateway_payment_id,
    )
    return True


async def record_staff_payment(
    db: AsyncSession,
    *,
    student_id: int,
    group_id: int,
    period: date,
    amount_minor: int,
    paid_on: Optional[date],
    note: Optional[str],
    recorded_by: int,
) -> int:
    """Отметить оплату руками — сразу подтверждённой, без чека.

    Нужна для двух живых случаев: месяц уже оплатили до того, как система
    появилась, и человек не разобрался с кабинетом, а деньги прислал.

    Подтверждать нечего — решение принимает тот, кто отмечает, поэтому он же
    записан в `reviewed_by`. Примечание обязательно осмысленное: платёж без
    чека нечем подтвердить, кроме этой строки, и через полгода она будет
    единственным объяснением, откуда взялись деньги.
    """
    row = (
        await db.execute(
            text(
                """
                INSERT INTO student_payment
                       (student_id, group_id, period, amount_minor, method,
                        paid_on, status, reviewed_at, reviewed_by, review_note)
                VALUES (:s, :g, :p, :amt, 'manual',
                        :paid_on, 'confirmed', now(), :by, :note)
                RETURNING id
                """
            ),
            {
                "s": student_id,
                "g": group_id,
                "p": period,
                "amt": amount_minor,
                "paid_on": paid_on,
                "by": recorded_by,
                "note": note,
            },
        )
    ).one()
    await db.commit()
    logger.info(
        "tsk-010: оплата отмечена вручную — платёж %s, ученик %s, %s, %s коп., отметил %s",
        row.id,
        student_id,
        period,
        amount_minor,
        recorded_by,
    )
    return int(row.id)


async def charge_by_id(db: AsyncSession, *, charge_id: int) -> Optional[dict]:
    """Начисление по номеру — для сверки данных, пришедших от шлюза."""
    row = (
        await db.execute(
            text(
                "SELECT id, student_id, group_id, period, status "
                "  FROM student_monthly_charge WHERE id = :id"
            ),
            {"id": charge_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def student_names(db: AsyncSession, *, student_ids: list[int]) -> dict[int, str]:
    """ФИО учеников по номерам — для разбора платежей, которых нет в учёте.

    Номер ученика в таких платежах приходит из метаданных шлюза, строки платежа
    у нас нет, и обычные соединения по учёту здесь не помогают. Запрос один на
    весь список, а не по платежу: сверка идёт за месяц целиком (tsk-616).
    """
    if not student_ids:
        return {}
    rows = (
        await db.execute(
            text("SELECT id, full_name FROM users WHERE id = ANY(:ids)"),
            {"ids": student_ids},
        )
    ).all()
    return {int(row.id): row.full_name for row in rows}


async def list_payments(
    db: AsyncSession,
    *,
    status: Optional[str] = None,
    period: Optional[date] = None,
    student_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    purpose: Optional[str] = None,
) -> list[dict]:
    """Платежи для кабинета маркетолога: очередь и история.

    Каждая строка несёт и сумму месяца, и остаток по нему. Без этого решение
    принимается вслепую: платёж на 55 000 вместо 5 500 и два одинаковых чека
    подряд выглядят на экране совершенно нормально, если не с чем сравнить.

    Фильтры складываются в самом запросе, а не постфильтром в Python: иначе
    ограничение выборки молча срезало бы часть строк ещё до фильтрации.

    tsk-615: разовые покупки идут в том же списке — это и есть деньги школы, а
    не отдельная касса. Начисление и тарифная группа у них пусты, поэтому оба
    соединения ЛЕВЫЕ: внутреннее молча выбрасывало бы такие строки из списка, и
    учёт остался бы ровно так же невидим, как до этой задачи.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT p.id,
                       p.student_id,
                       u.full_name,
                       p.group_id,
                       pg.name AS group_name,
                       p.period,
                       p.purpose,
                       p.amount_minor,
                       p.method,
                       p.status,
                       p.receipt_name,
                       (p.receipt_file IS NOT NULL) AS has_receipt,
                       p.payer_note,
                       p.paid_on,
                       p.gateway,
                       p.gateway_payment_id,
                       p.review_note,
                       p.reviewed_at,
                       reviewer.full_name AS reviewed_by_name,
                       p.created_at,
                       -- Сумма месяца и что по нему уже подтверждено: то, без
                       -- чего расхождение на экране не увидеть. Итог месяца
                       -- (COALESCE(manual, calculated) + поправки) считается
                       -- ниже в Python через charge_service.charge_total_minor
                       -- — та же формула, что у списка начислений и напоминаний.
                       ch.calculated_minor,
                       ch.manual_minor,
                       COALESCE(adj.total, 0)       AS adjustments_minor,
                       COALESCE(paid.total, 0)      AS charge_paid_minor
                  FROM student_payment p
                  JOIN users u ON u.id = p.student_id
                  LEFT JOIN pricing_group pg ON pg.id = p.group_id
                  LEFT JOIN student_monthly_charge ch
                       ON ch.student_id = p.student_id
                      AND ch.group_id = p.group_id
                      AND ch.period = p.period
                  LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total
                          FROM charge_adjustment a
                         WHERE a.student_id = p.student_id
                           AND a.group_id = p.group_id
                           AND a.period = p.period
                  ) adj ON TRUE
                  LEFT JOIN LATERAL (
                        SELECT sum(x.amount_minor) AS total
                          FROM student_payment x
                         WHERE x.student_id = p.student_id
                           AND x.group_id = p.group_id
                           AND x.period = p.period
                           AND x.status = 'confirmed'
                  ) paid ON TRUE
                 -- CAST на параметрах: см. _totals_by_charge, необязательный
                 -- фильтр без явного типа asyncpg не разбирает.
                 WHERE (CAST(:st AS text) IS NULL OR p.status = CAST(:st AS text))
                   -- Фильтр месяца работает по-разному для двух видов денег, и
                   -- иначе нельзя: у разовой покупки месяца нет вовсе, а сравнение
                   -- `period = :p` для неё никогда не истинно — покупки молча
                   -- выпадали бы из сводки за месяц, то есть остались бы ровно так
                   -- же невидимы, как до tsk-615. Для них месяц — это месяц, когда
                   -- деньги пришли.
                   AND (
                        CAST(:p AS date) IS NULL
                        OR (p.purpose =  'monthly' AND p.period = CAST(:p AS date))
                        OR (p.purpose <> 'monthly'
                            AND date_trunc(
                                    'month',
                                    COALESCE(p.paid_on, p.created_at::date)
                                )::date = CAST(:p AS date))
                   )
                   AND (CAST(:s AS integer) IS NULL OR p.student_id = CAST(:s AS integer))
                   AND (CAST(:id AS integer) IS NULL OR p.id = CAST(:id AS integer))
                   AND (CAST(:pu AS text) IS NULL OR p.purpose = CAST(:pu AS text))
                 ORDER BY (p.status = 'pending') DESC, p.created_at DESC
                """
            ),
            {
                "st": status,
                "p": period,
                "s": student_id,
                "id": payment_id,
                "pu": purpose,
            },
        )
    ).all()
    result: list[dict] = []
    for r in rows:
        row = dict(r._mapping)
        calculated_minor = row.pop("calculated_minor")
        manual_minor = row.pop("manual_minor")
        adjustments_minor = int(row.pop("adjustments_minor"))
        paid = int(row.pop("charge_paid_minor"))
        row["purpose_name"] = PURPOSE_NAMES.get(row["purpose"], row["purpose"])
        if calculated_minor is None:
            # Разовая покупка: месяца нет, значит нет ни суммы месяца, ни
            # остатка по нему. Ноль тут был бы хуже пустоты — он читается как
            # «месяц оплачен полностью» и превратил бы экран в неправду.
            row["charge_total_minor"] = None
            row["charge_due_minor"] = None
            result.append(row)
            continue
        total = charge_service.charge_total_minor(
            calculated_minor=calculated_minor,
            manual_minor=manual_minor,
            adjustments_minor=adjustments_minor,
        )
        row["charge_total_minor"] = total
        # Остаток — фактический на сейчас: у ожидающего платежа он ещё не
        # учитывает его самого (видно, что закроется по нажатию «получены»), у
        # подтверждённого — уже учитывает, и это верно для истории.
        row["charge_due_minor"] = max(total - paid, 0)
        result.append(row)
    return result


async def _decide(
    db: AsyncSession,
    *,
    payment_id: int,
    new_status: str,
    reviewed_by: int,
    note: Optional[str],
) -> Optional[dict]:
    """Общий путь подтверждения и отклонения.

    Условие `status = 'pending'` стоит в самом UPDATE: два маркетолога,
    открывшие очередь одновременно, не должны переписать решение друг друга —
    второй получит «уже обработан», а не тихо затрёт первое.
    """
    row = (
        await db.execute(
            text(
                "UPDATE student_payment "
                "   SET status = :st, reviewed_by = :by, reviewed_at = now(), "
                "       review_note = :note, updated_at = now() "
                " WHERE id = :id AND status = 'pending' "
                "RETURNING id, student_id, group_id, period, amount_minor, status"
            ),
            {"id": payment_id, "st": new_status, "by": reviewed_by, "note": note},
        )
    ).first()
    if row is None:
        await db.rollback()
        return None
    await db.commit()
    logger.info(
        "tsk-010: платёж %s → %s (решил %s)", payment_id, new_status, reviewed_by
    )
    return dict(row._mapping)


async def confirm_payment(
    db: AsyncSession, *, payment_id: int, reviewed_by: int, note: Optional[str] = None
) -> Optional[dict]:
    """Подтвердить платёж: деньги считаются полученными.

    Напоминание про чек «Мой налог» живёт на стороне кабинета — система его не
    выбивает и не должна делать вид, что процесс на этом закончен.
    """
    return await _decide(
        db,
        payment_id=payment_id,
        new_status="confirmed",
        reviewed_by=reviewed_by,
        note=note,
    )


async def reject_payment(
    db: AsyncSession, *, payment_id: int, reviewed_by: int, note: Optional[str] = None
) -> Optional[dict]:
    """Отклонить платёж — например, чек не читается или уже был учтён."""
    return await _decide(
        db,
        payment_id=payment_id,
        new_status="rejected",
        reviewed_by=reviewed_by,
        note=note,
    )


async def get_receipt(db: AsyncSession, *, payment_id: int) -> Optional[dict]:
    """Строка платежа для выдачи файла чека."""
    row = (
        await db.execute(
            text(
                "SELECT id, student_id, receipt_file, receipt_name "
                "  FROM student_payment WHERE id = :id"
            ),
            {"id": payment_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def export_confirmed(
    db: AsyncSession, *, date_from: date, date_to: date
) -> list[dict]:
    """Подтверждённые платежи за период — для сверки с чеками «Мой налог».

    Дата берётся по дню платежа, а не по дню подтверждения: сверяем с тем, когда
    деньги реально пришли. Если день платежа не указан, подставляем день
    заведения записи — иначе такой платёж выпал бы из выгрузки совсем.

    tsk-615: разовые покупки входят в выгрузку наравне с месяцами — именно по
    ней сверяют приход со шлюзом, и пропуск класса платежей означал бы, что
    сумма в ЮKassa всегда больше, чем в системе. Соединение с тарифной группой
    ЛЕВОЕ: у покупки группы нет, а внутреннее её бы отбросило.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT p.id,
                       COALESCE(p.paid_on, p.created_at::date) AS on_date,
                       u.full_name,
                       pg.name AS group_name,
                       p.period,
                       p.purpose,
                       p.amount_minor,
                       p.method,
                       p.gateway,
                       p.gateway_payment_id,
                       p.reviewed_at
                  FROM student_payment p
                  JOIN users u ON u.id = p.student_id
                  LEFT JOIN pricing_group pg ON pg.id = p.group_id
                 WHERE p.status = 'confirmed'
                   AND COALESCE(p.paid_on, p.created_at::date) BETWEEN :d1 AND :d2
                 ORDER BY on_date, u.full_name
                """
            ),
            {"d1": date_from, "d2": date_to},
        )
    ).all()
    return [
        {**dict(r._mapping), "purpose_name": PURPOSE_NAMES.get(r.purpose, r.purpose)}
        for r in rows
    ]


async def student_ids_for_parent(db: AsyncSession, *, parent_id: int) -> list[int]:
    """Дети родителя — чьи начисления и платежи ему видны."""
    rows = (
        await db.execute(
            text("SELECT student_id FROM parent_student_links WHERE parent_id = :p"),
            {"p": parent_id},
        )
    ).all()
    return [int(r.student_id) for r in rows]
