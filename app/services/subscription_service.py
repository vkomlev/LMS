"""Автоприсвоение тарифа (tsk-301, решение оператора 2026-08-08).

Два правила, оба выведены из живого прогона Фазы 6:

1. **Регистрация → `demo`.** Без этого каждый новый ученик остаётся без тарифа, а
   при включённом гейте это означает отказ: человек в первый же день теряет
   ИИ-наставника и кнопку помощи преподавателю. Регистраций примерно одна в день,
   и узнать о такой потере можно было бы только по жалобе.
2. **Добавили в расписание → `base`.** Появление занятий и есть признак того, что
   человек стал учеником по-настоящему.

**Второе правило срабатывает ТОЛЬКО с `demo` (или при полном отсутствии тарифа).**
Это не перестраховка: на `base_legacy` сидят 37 действующих учеников со СТАРОЙ ценой
(2750/5500), а `base` — это «Базовый 2026» (3000/6000). Правило без такой оговорки
поднимало бы цену каждому, кому меняют расписание, — молча и задним числом, потому
что смена расписания и так зовёт пересчёт открытого месяца (tsk-548).

Понижения нет: снятие с расписания тариф не трогает. Отобрать права автоматически
опаснее, чем выдать, и «перестал ходить» ≠ «перестал учиться».
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pricing_service

logger = logging.getLogger(__name__)

#: Тариф по умолчанию при регистрации.
DEFAULT_PLAN_CODE = "demo"

#: Тариф, на который переводит появление занятий в расписании.
SCHEDULED_PLAN_CODE = "base"

#: С каких тарифов расписание повышает. Пустой набор означал бы «с любого», а это
#: и есть тот случай, когда давнему клиенту молча меняют цену.
UPGRADABLE_FROM: frozenset[str] = frozenset({DEFAULT_PLAN_CODE})


async def _current_plan_code(db: AsyncSession, student_id: int) -> Optional[str]:
    return (
        await db.execute(
            text(
                "SELECT p.code FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :sid AND s.ends_on IS NULL"
            ),
            {"sid": student_id},
        )
    ).scalar()


async def _assign(
    db: AsyncSession,
    student_id: int,
    plan_code: str,
    *,
    reason: str,
    changed_by: Optional[int] = None,
) -> bool:
    """Открыть подписку на план. Возвращает False, если план не найден.

    Гонка двух одновременных входов закрывается частичным уникальным индексом
    «одна действующая подписка», а не проверкой в коде: между `SELECT` и `INSERT`
    успевает пройти второй запрос.
    """
    row = (
        await db.execute(
            text(
                "SELECT id, pricing_group_id FROM subscription_plan "
                " WHERE code = :c AND is_active"
            ),
            {"c": plan_code},
        )
    ).first()
    if row is None:
        logger.error(
            "tsk-301: тариф %s не найден — ученик %s остался без автоприсвоения",
            plan_code, student_id,
        )
        return False

    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_subscription "
                    "  (student_id, plan_id, pricing_group_id, starts_on, reason, "
                    "   changed_by) "
                    "VALUES (:s, :p, :g, CURRENT_DATE, :r, :by)"
                ),
                {
                    "s": student_id,
                    "p": row.id,
                    "g": row.pricing_group_id,
                    "r": reason,
                    "by": changed_by,
                },
            )
    except IntegrityError:
        # Подписка появилась параллельно — это не ошибка, а именно то, чего мы
        # хотели. Savepoint не даёт откату отравить внешнюю транзакцию (урок Y-1.5).
        logger.info(
            "tsk-301: подписка ученика %s создана параллельно, автоприсвоение пропущено",
            student_id,
        )
        return False

    logger.info(
        "tsk-301: ученику %s присвоен тариф %s (%s)", student_id, plan_code, reason
    )
    return True


async def ensure_default_plan(
    db: AsyncSession, student_id: int, *, channel: str
) -> bool:
    """Дать тариф по умолчанию, если тарифа нет. Идемпотентно.

    Вызывается там же, где проставляется роль `student` — чтобы «зарегистрировался»
    и «получил права» были одним событием, а не двумя, между которыми человек
    видит отказ.
    """
    if await _current_plan_code(db, student_id) is not None:
        return False
    return await _assign(
        db, student_id, DEFAULT_PLAN_CODE, reason=f"tsk-301 авто: регистрация ({channel})"
    )


async def upgrade_on_schedule(db: AsyncSession, student_id: int) -> bool:
    """Перевести на `base`, если ученика добавили в расписание.

    Повышает **только** с `demo` и с «тарифа нет». Любой другой тариф остаётся как
    есть: на `base_legacy` держится старая цена 37 действующих учеников, и молча
    переводить их на новую нельзя. `test`, `flagship`, `adults`, `alumni` тоже
    назначены осознанно — расписание не повод их переписывать.
    """
    current = await _current_plan_code(db, student_id)
    if current is not None and current not in UPGRADABLE_FROM:
        return False

    has_slot = (
        await db.execute(
            text(
                "SELECT 1 FROM lesson_slot_student lss "
                "  JOIN lesson_slot ls ON ls.id = lss.slot_id "
                " WHERE lss.student_id = :sid AND lss.is_active AND ls.is_active "
                " LIMIT 1"
            ),
            {"sid": student_id},
        )
    ).first()
    if has_slot is None:
        return False

    return await change_plan(
        db,
        student_id,
        SCHEDULED_PLAN_CODE,
        reason="tsk-301 авто: появилось занятие в расписании",
    )


async def change_plan(
    db: AsyncSession,
    student_id: int,
    plan_code: str,
    *,
    reason: str,
    changed_by: Optional[int] = None,
) -> bool:
    """Перевести ученика на другой тариф: закрыть действующую строку, открыть новую.

    Единственный штатный путь смены. Прямой `UPDATE student_subscription SET
    plan_id = …` запрещён: история тарифов держится строками, а не полем
    «предыдущий» (см. `StudentSubscription`), и правка на месте стирает, по какой
    группе считался прошлый месяц.

    Закрытие и открытие идут одним savepoint. Порознь их разорвало бы на гонке:
    вставка падает на частичном уникальном индексе, а старая строка уже закрыта —
    ученик остаётся без действующей подписки, то есть без прав, и заметно это
    станет по жалобе.

    **Деньги не пересчитываются здесь намеренно.** Смена тарифа меняет группу
    на будущее; переписывать уже названную человеку сумму текущего месяца —
    отдельный спорный вопрос (tsk-585, решение 14). Возвращает False, если план
    не найден или подписку сменили параллельно.
    """
    # Перевод на тариф, который уже действует, — пустая операция. Без этого
    # повторная доставка уведомления плодила бы строки истории: закрыла бы
    # действующую и открыла точно такую же (tsk-301, Фаза 8).
    if await _current_plan_code(db, student_id) == plan_code:
        return False

    savepoint = await db.begin_nested()
    try:
        await db.execute(
            text(
                "UPDATE student_subscription SET ends_on = CURRENT_DATE "
                " WHERE student_id = :sid AND ends_on IS NULL"
            ),
            {"sid": student_id},
        )
        assigned = await _assign(
            db, student_id, plan_code, reason=reason, changed_by=changed_by
        )
        if not assigned:
            await savepoint.rollback()
            return False
    except Exception:
        if savepoint.is_active:
            await savepoint.rollback()
        raise
    await savepoint.commit()
    return True


async def grant_ai_package(
    db: AsyncSession,
    student_id: int,
    *,
    units: int,
    gateway_payment_id: Optional[str] = None,
    granted_by: Optional[int] = None,
    note: Optional[str] = None,
) -> bool:
    """Зачислить купленный пакет обращений к наставнику.

    Идемпотентность держит уникальный `gateway_payment_id`, а не проверка в коде:
    платёжный сервис повторяет доставку уведомления, пока не получит 200, и две
    доставки приходят одновременно чаще, чем кажется. Повтор — не ошибка, а
    штатный исход: возвращаем False, и вызывающий отвечает «уже зачислено».

    Ручная выдача персоналом идёт без номера платежа — тогда идемпотентности нет
    и быть не может: две одинаковые выдачи это две разные договорённости.

    Returns:
        True — пакет зачислен сейчас; False — этот платёж уже зачислен раньше.
    """
    if units <= 0:
        raise ValueError("объём пакета должен быть положительным")

    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO student_ai_grant "
                    "  (student_id, granted, gateway_payment_id, granted_by, note) "
                    "VALUES (:s, :g, :p, :by, :n)"
                ),
                {
                    "s": student_id,
                    "g": units,
                    "p": gateway_payment_id,
                    "by": granted_by,
                    "n": note,
                },
            )
    except IntegrityError:
        logger.info(
            "tsk-301: пакет по платежу %s уже зачислен ученику %s — повтор доставки",
            gateway_payment_id, student_id,
        )
        return False

    logger.info(
        "tsk-301: ученику %s зачислен пакет на %s обращений (платёж %s)",
        student_id, units, gateway_payment_id or "выдан вручную",
    )
    return True


async def record_ai_package_purchase(
    db: AsyncSession,
    student_id: int,
    *,
    units: int,
    gateway_payment_id: str,
    amount_minor: int,
    paid_on: Optional[date] = None,
) -> tuple[bool, bool]:
    """Зачислить оплаченный пакет И учесть деньги за него (tsk-615).

    Пакет и платёж — две половины одного события, поэтому они здесь вместе и в
    одной транзакции: до tsk-615 зачислялся только пакет, и первая же живая
    покупка (500 ₽, 16.08.2026) осталась вне учёта — в ЮKassa деньги были, в
    LMS их не было нигде.

    Платёж пишется БЕЗ месяца: покупка бессрочная и к месяцу не относится.
    Коммит происходит внутри записи платежа и закрепляет обе вставки разом —
    иначе сбой между ними оставил бы пакет без денег или деньги без пакета.

    Повтор доставки уведомления безопасен с любой стороны: у пакета уникален
    номер платежа, у платежа — пара «шлюз + номер». Если пакет уже был, а
    платёж почему-то нет (так выглядят покупки до этой задачи), запишется
    только платёж — ровно то, что нужно для сверки.

    Returns:
        Пара «зачислен ли пакет сейчас, записан ли платёж сейчас».
    """
    from app.services import payment_service  # noqa: PLC0415

    granted = await grant_ai_package(
        db, student_id, units=units,
        gateway_payment_id=gateway_payment_id, note="оплачено картой",
    )
    recorded = await payment_service.record_gateway_payment(
        db,
        student_id=student_id,
        group_id=None,
        period=None,
        amount_minor=amount_minor,
        gateway="yookassa",
        gateway_payment_id=gateway_payment_id,
        paid_on=paid_on or date.today(),
        purpose="ai_package",
        review_note="Пакет обращений к наставнику, оплачен картой",
    )
    if not granted and not recorded:
        logger.info(
            "tsk-615: платёж %s за пакет уже учтён целиком — повтор доставки",
            gateway_payment_id,
        )
    return granted, recorded


#: Тарифы, которые человек может купить сам. Признак — есть тарифная группа
#: (значит, есть цена) и НЕТ занятий: расписание заводит методист, и продавать
#: его через кнопку нельзя — обещание, которое некому выполнить.
async def purchasable_plans(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT p.code, p.name, p.upgrade_hint, p.ai_tutor_limit, "
                "       t.price_minor, g.name AS group_name "
                "  FROM subscription_plan p "
                "  JOIN pricing_group g ON g.id = p.pricing_group_id "
                "  JOIN pricing_tariff t ON t.group_id = g.id AND t.is_active "
                "                       AND t.match_kind IS NULL "
                " WHERE p.is_active AND NOT p.lessons "
                " ORDER BY t.price_minor"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def purchase_plan(
    db: AsyncSession,
    student_id: int,
    plan_code: str,
    *,
    gateway_payment_id: str,
    amount_minor: int,
    today: Optional[date] = None,
) -> bool:
    """Зачислить оплаченную подписку: права сразу, деньги — по правилу месяца.

    Замыкает круг «оплатил → начислено → доступ». Разорвать его в любом месте
    значит либо дать доступ даром, либо взять деньги и не дать ничего.

    **Правило первого месяца** (решение оператора 2026-08-08): покупка до 20-го
    числа включительно оплачивает текущий месяц; позже — первое начисление
    ставится за следующий, а остаток текущего даётся бесплатно. Человек не
    должен платить полную цену за три дня ровно в тот момент, когда впервые
    расстаётся с деньгами.

    Идемпотентность держит уникальность платежа в `student_payment`: повторная
    доставка уведомления вернёт False и ничего не изменит.

    Returns:
        True — подписка выдана и платёж зачтён; False — этот платёж уже учтён.
    """
    from app.core.config import Settings  # noqa: PLC0415
    from app.services import charge_service, payment_service  # noqa: PLC0415

    settings = Settings()
    today = today or date.today()

    # Правило продажи ровно то же, что и в витрине `purchasable_plans`: есть
    # цена И нет занятий. Ослабить его здесь нельзя — сервис последняя линия, а
    # код тарифа приходит из тела уведомления, то есть снаружи. Первая редакция
    # проверяла только наличие цены, и через подделанное поле можно было бы
    # получить «Базовый» по цене Self (поймано тестом до выката).
    plan = (
        await db.execute(
            text(
                "SELECT id, pricing_group_id FROM subscription_plan "
                " WHERE code = :c AND is_active AND pricing_group_id IS NOT NULL "
                "   AND NOT lessons"
            ),
            {"c": plan_code},
        )
    ).first()
    if plan is None:
        raise ValueError(f"тариф {plan_code!r} нельзя купить самостоятельно")

    group_id = int(plan.pricing_group_id)
    period = (
        date(today.year, today.month, 1)
        if today.day <= settings.first_month_charge_cutoff_day
        else charge_service.next_month(date(today.year, today.month, 1))
    )

    # Порядок продиктован схемой: `student_payment` ссылается на строку
    # начисления внешним ключом, поэтому записать платёж раньше, чем появится
    # начисление, нельзя. Значит замком идемпотентности платёж быть не может —
    # проверяем его существование ЯВНО, до всякой работы.
    already = (
        await db.execute(
            text(
                "SELECT 1 FROM student_payment "
                " WHERE gateway = 'yookassa' AND gateway_payment_id = :txn"
            ),
            {"txn": gateway_payment_id},
        )
    ).first()
    if already is not None:
        logger.info(
            "tsk-301: платёж %s за подписку уже учтён — повтор доставки",
            gateway_payment_id,
        )
        return False

    # Ранняя проверка выше не отменяет уникальный индекс ниже: между ними
    # проходит гонка двух одновременных доставок (урок tsk-574).
    await change_plan(
        db, student_id, plan_code,
        reason=f"tsk-301: самостоятельная покупка, платёж {gateway_payment_id}",
    )
    await db.commit()

    # Начисление создаём ПОСЛЕ выдачи тарифа: расчёт берёт группу из
    # действующей подписки, а до смены её там ещё нет. Считаем ИМЕННО целевой
    # период: «открытые месяцы» следующий месяц не покрывают, а при покупке
    # после порога платёж относится как раз к нему.
    await charge_service.recalculate_student_group(
        db, student_id=student_id, group_id=group_id, period=period
    )
    await db.commit()

    recorded = await payment_service.record_gateway_payment(
        db,
        student_id=student_id,
        group_id=group_id,
        period=period,
        amount_minor=amount_minor,
        gateway="yookassa",
        gateway_payment_id=gateway_payment_id,
        paid_on=today,
    )
    if not recorded:
        # Гонка: параллельная доставка успела записать платёж. Тариф уже выдан
        # — это то же самое состояние, к которому шла и она.
        logger.info(
            "tsk-301: платёж %s записан параллельной доставкой", gateway_payment_id
        )
        return False
    await db.commit()
    logger.info(
        "tsk-301: ученику %s выдана подписка %s за %s ₽, период %s",
        student_id, plan_code, amount_minor // 100, period,
    )
    return True


# ─────────────── Управление тарифами персоналом (Фаза 9) ────────────────────

#: С какого дня на одном тарифе человек считается «засидевшимся» (tsk-619).
#: Месяц — не круглое число ради красоты: `demo` даётся при регистрации, а
#: `base` при появлении расписания, и месяц между ними означает, что расписание
#: так и не завели.
LONG_STANDING_DAYS = 30

#: Имя строки «без тарифа» в сводке. Живёт здесь, а не на экране: строка
#: приходит из того же списка, что и тарифы, и клиент не должен догадываться,
#: как назвать пустое значение.
NO_PLAN_ROW_NAME = "Без тарифа"


async def list_plans(db: AsyncSession) -> list[dict]:
    """Все действующие тарифы с правами и тарифной группой — витрина персонала.

    Отличается от `purchasable_plans` не оформлением, а смыслом: там витрина
    покупки (только то, что человек может купить сам), здесь — весь набор, из
    которого персонал присваивает, включая `test`, `base_legacy` и «Выпускник».
    Свести их в один список нельзя: продавать `test` некому, а присвоить —
    единственный способ его выдать.

    Группа отдаётся именем, а не только номером: маркетолог принимает решение о
    деньгах, и «группа 6» ему ни о чём не говорит.
    """
    rows = (
        await db.execute(
            text(
                "SELECT p.code, p.name, p.ai_tutor_limit, p.code_review, "
                "       p.teacher_escalation, p.lessons, p.content, "
                "       p.pricing_group_id, g.name AS pricing_group_name, "
                "       p.upgrade_hint, p.sort_order "
                "  FROM subscription_plan p "
                "  LEFT JOIN pricing_group g ON g.id = p.pricing_group_id "
                " WHERE p.is_active "
                " ORDER BY p.sort_order, p.code"
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _staff_student_rows(db: AsyncSession) -> list[dict]:
    """Каждый ученик школы с его действующим тарифом — общее основание сводки.

    Один запрос на обе выдачи (счётчики и разворот строки) намеренно: разойдись
    они, в сводке стояло бы «на Demo трое», а по нажатию открывалось бы двое, и
    доверия к экрану не осталось бы (урок tsk-597/598 — общее условие отбора
    зовут функцией, а не копируют).

    «Ученик» здесь тот же, что и в поиске маркетолога (`lead_service`): активная
    учётка с ролью `student`. Это не педантизм: из строки сводки человек идёт в
    ту же панель, где ученика находят поиском, — списки обязаны совпадать.
    Подписка неактивной учётки в счёт не идёт (на проде такая есть — 4558).

    `LEFT JOIN` по действующей строке подписки: ученик без тарифа обязан попасть
    в выдачу, он и есть главный адресат этой сводки.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT u.id                  AS student_id,
                       u.full_name,
                       u.created_at::date    AS registered_on,
                       p.code                AS plan_code,
                       p.name                AS plan_name,
                       s.pricing_group_id,
                       g.name                AS pricing_group_name,
                       s.starts_on           AS plan_since,
                       EXISTS (
                           SELECT 1
                             FROM lesson_slot_student lss
                             JOIN lesson_slot ls ON ls.id = lss.slot_id
                            WHERE lss.student_id = u.id
                              AND lss.is_active AND ls.is_active
                       )                     AS has_schedule
                  FROM users u
                  LEFT JOIN student_subscription s
                         ON s.student_id = u.id AND s.ends_on IS NULL
                  LEFT JOIN subscription_plan p ON p.id = s.plan_id
                  LEFT JOIN pricing_group g ON g.id = s.pricing_group_id
                 WHERE u.is_active
                   AND EXISTS (
                       SELECT 1 FROM user_roles ur
                         JOIN roles r ON r.id = ur.role_id AND r.name = 'student'
                        WHERE ur.user_id = u.id
                   )
                 ORDER BY u.full_name, u.id
                """
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _overdue_student_ids(db: AsyncSession) -> set[int]:
    """Кто просрочил оплату — множеством номеров.

    Считает не этот модуль: зовём `payment_reminder_service.list_overdue`, тот же
    источник, что рассылает письма о долге и красит бейдж в кабинете. Своя
    SQL-версия «есть долг» стала бы четвёртой копией формулы «ручная сумма
    важнее расчётной, поверх поправки» (см. `charge_service.charge_total_minor`)
    и разъехалась бы с ней на первой же правке.
    """
    from app.services import payment_reminder_service  # noqa: PLC0415

    return {debtor.student_id for debtor in await payment_reminder_service.list_overdue(db)}


def _days_on_plan(plan_since: Optional[date], today: date) -> Optional[int]:
    """Сколько дней человек на текущем тарифе. None — тарифа нет."""
    if plan_since is None:
        return None
    return (today - plan_since).days


async def plan_distribution(db: AsyncSession, *, today: Optional[date] = None) -> dict:
    """Сколько учеников на каждом тарифе — и сколько без тарифа вовсе (tsk-619).

    Отвечает на вопрос, которого не было у панели Фазы 9: та знает всё про
    одного человека, но не про то, кого вообще стоит открыть. Между
    авто-`demo` при регистрации и авто-`base` при появлении расписания человек
    живёт неделями, и увидеть его было неоткуда.

    Пустые тарифы остаются в выдаче строками с нулём: «на Self никого» — это
    ответ, а не отсутствие ответа, и пропасть он не должен.

    Разрезы выбраны по действию, которое за ними следует:

    * **расписание** — им автоматика и меряет «стал учеником». `demo` с
      расписанием значит, что перевод на `base` не сработал; `base` без
      расписания — что человек перестал ходить, а деньги считаются;
    * **давность на тарифе** — «сидит на Demo второй месяц» и «зарегистрировался
      вчера» требуют разного, а выглядят в счётчике одинаково;
    * **просроченная оплата** — берётся у того же источника, что и письма о
      долге, поэтому спорить с рассылкой этот счётчик не может.

    Сумма денег сюда НЕ идёт: её считает расписание, а не тариф (ADR-0006), и
    второй ответ на вопрос «сколько должен» разъехался бы с экраном начислений.
    """
    today = today or date.today()
    students = await _staff_student_rows(db)
    overdue = await _overdue_student_ids(db)
    plans = await list_plans(db)

    #: Строка «без тарифа» стоит последней и существует всегда, даже пустая:
    #: она и есть главный вопрос сводки, а исчезнув при нуле, была бы
    #: неотличима от «мы это не считаем».
    buckets: list[tuple[Optional[str], str, Optional[int], Optional[str]]] = [
        (p["code"], p["name"], p["pricing_group_id"], p["pricing_group_name"])
        for p in plans
    ]

    # Выключенный тариф, который у кого-то ещё ДЕЙСТВУЕТ, обязан остаться
    # строкой. Витрина `list_plans` отдаёт только активные, и без этой добавки
    # такой ученик не попал бы ни в одну строку: итог перестал бы сходиться с
    # суммой, причём молча — а деактивируют тариф ровно тогда, когда хотят
    # посмотреть, кто на нём ещё сидит. Имя и группа берутся из строки ученика:
    # у него это группа ЕГО подписки, то есть ровно то, по чему ему считают
    # месяц, — для выключенного тарифа это точнее справочника.
    known = {code for code, *_ in buckets}
    for student in students:
        code = student["plan_code"]
        if code is None or code in known:
            continue
        known.add(code)
        buckets.append(
            (
                code,
                f"{student['plan_name']} (тариф выключен)",
                student["pricing_group_id"],
                student["pricing_group_name"],
            )
        )

    buckets.append((None, NO_PLAN_ROW_NAME, None, None))

    rows: list[dict] = []
    for code, name, group_id, group_name in buckets:
        members = [s for s in students if s["plan_code"] == code]
        days = [
            d
            for d in (_days_on_plan(s["plan_since"], today) for s in members)
            if d is not None
        ]
        starts = [s["plan_since"] for s in members if s["plan_since"] is not None]
        rows.append(
            {
                "plan_code": code,
                "plan_name": name,
                "pricing_group_id": group_id,
                "pricing_group_name": group_name,
                "students": len(members),
                "with_schedule": sum(1 for s in members if s["has_schedule"]),
                "without_schedule": sum(1 for s in members if not s["has_schedule"]),
                "long_standing": sum(1 for d in days if d >= LONG_STANDING_DAYS),
                "oldest_started_on": min(starts) if starts else None,
                "with_overdue_payment": sum(
                    1 for s in members if s["student_id"] in overdue
                ),
            }
        )

    return {
        "as_of": today,
        "total_students": len(students),
        "long_standing_days": LONG_STANDING_DAYS,
        "rows": rows,
    }


async def students_on_plan(
    db: AsyncSession, plan_code: Optional[str], *, today: Optional[date] = None
) -> list[dict]:
    """Ученики одной строки сводки. `plan_code=None` — строка «без тарифа».

    Существует ради того, чтобы сводка не осталась картинкой: из строки
    открывается список людей, из списка — та же панель тарифа, что и раньше.

    Дольше всех на тарифе — сверху: именно этот конец списка и есть повод для
    работы («второй месяц на Demo»), а алфавит прячет его в середину.
    """
    today = today or date.today()
    overdue = await _overdue_student_ids(db)
    members = [s for s in await _staff_student_rows(db) if s["plan_code"] == plan_code]

    result = [
        {
            "student_id": s["student_id"],
            "full_name": s["full_name"],
            "plan_since": s["plan_since"],
            "days_on_plan": _days_on_plan(s["plan_since"], today),
            "registered_on": s["registered_on"],
            "has_schedule": s["has_schedule"],
            "has_overdue_payment": s["student_id"] in overdue,
        }
        for s in members
    ]
    # Ключ сортировки развёрнут в функцию, а не `or -1`: у человека, которому
    # тариф присвоили сегодня, `days_on_plan == 0` — а ноль ложен, и короткая
    # запись отправила бы его в один разряд с «тарифа нет». None в сравнении с
    # int роняет сортировку целиком, поэтому подменять его всё равно надо.
    def _order(row: dict) -> tuple[int, str]:
        days = row["days_on_plan"]
        return (-1 if days is None else -days, row["full_name"] or "")

    result.sort(key=_order)
    return result


async def student_state(db: AsyncSession, student_id: int) -> dict:
    """Действующий тариф ученика и вся история присвоений.

    История возвращается целиком и в обратном порядке: вопрос персонала звучит
    как «почему у него такой тариф», и ответ на него — предыдущая строка с
    причиной и автором, а не текущая.
    """
    rows = (
        await db.execute(
            text(
                "SELECT s.id, p.code AS plan_code, p.name AS plan_name, "
                "       s.starts_on, s.ends_on, s.reason, s.changed_by, "
                "       u.full_name AS changed_by_name, "
                "       s.pricing_group_id, g.name AS pricing_group_name "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                "  LEFT JOIN users u ON u.id = s.changed_by "
                "  LEFT JOIN pricing_group g ON g.id = s.pricing_group_id "
                " WHERE s.student_id = :sid "
                " ORDER BY s.starts_on DESC, s.id DESC"
            ),
            {"sid": student_id},
        )
    ).mappings().all()
    history = [dict(r) for r in rows]
    current = next((h for h in history if h["ends_on"] is None), None)
    return {
        "student_id": student_id,
        "current": current,
        "history": history,
        "manual_pricing": await manual_pricing_state(db, student_id),
    }


async def manual_pricing_state(db: AsyncSession, student_id: int) -> dict:
    """Ручные деньги ученика — то, что затронет смена тарифа (tsk-634).

    Отдаётся рядом с тарифом намеренно: экран перевода — единственное место,
    где эту связь видно вовремя. Ручная цена ставится там, где есть личная
    договорённость, и её изменение не выглядит ошибкой — выглядит обычным
    пересчётом; заметить его можно, только сверив со счётом, который человеку
    назвали.

    Закрытые месяцы сюда не попадают: перевод их не трогает, и предупреждать о
    них значило бы пугать зря.
    """
    amounts = (
        await db.execute(
            text(
                "SELECT ch.period, ch.group_id, g.name AS group_name, "
                "       ch.manual_minor, ch.calculated_minor "
                "  FROM student_monthly_charge ch "
                "  JOIN pricing_group g ON g.id = ch.group_id "
                " WHERE ch.student_id = :sid AND ch.status = 'open' "
                "   AND ch.manual_minor IS NOT NULL "
                " ORDER BY ch.period"
            ),
            {"sid": student_id},
        )
    ).mappings().all()

    # Действующая группа берётся на ПЕРВОЕ ЧИСЛО текущего месяца — тем же
    # правилом, каким её берут деньги (tsk-585). Спросить «группу сегодня»
    # значило бы написать «цена действует» там, где месяц считается по другой.
    billing = set(
        await pricing_service.billing_group_ids(
            db, student_id=student_id, period=date.today().replace(day=1)
        )
    )
    prices = (
        await db.execute(
            text(
                "SELECT o.group_id, g.name AS group_name, o.price_minor, o.note "
                "  FROM student_price_override o "
                "  JOIN pricing_group g ON g.id = o.group_id "
                " WHERE o.student_id = :sid ORDER BY g.name"
            ),
            {"sid": student_id},
        )
    ).mappings().all()

    return {
        "monthly_amounts": [dict(r) for r in amounts],
        "group_prices": [
            {**dict(r), "applies_now": int(r["group_id"]) in billing} for r in prices
        ],
    }
