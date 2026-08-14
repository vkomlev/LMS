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
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
