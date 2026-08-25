"""tsk-673 — перевод ученика на тариф «Выпускник» как одно событие.

**Зачем.** До этой задачи перевод менял только строку подписки. Всё остальное,
что должно случиться, когда человек заканчивает учиться, оставалось на памяти
маркетолога — и не случалось. На боевых данных 25.08.2026 это видно прямо:
двое выпускников из пяти (4497 и 4500) по-прежнему числились в слотах
расписания, то есть попадали в списки явки, в сводку преподавателя и в счётчик
наполнения слота, хотя учиться уже не должны.

**Порядок действий здесь не косметический — он единственно возможный.**
Снятие с расписания у ученика без активных слотов лишает расчёт основания:
`charge_service.recalculate_student_group` не может выбрать вариант тарифа и
УДАЛЯЕТ открытую строку месяца, если по ней нет платежа. Ежедневный тик
(`charge_cron_service`) делает это сам, даже если пересчёт не звать руками.
Значит снятие с расписания у должника **стирает сам долг** — ровно та же ловушка,
из-за которой в tsk-010 отказались закрывать доступ через `user_courses`.
Доказательство на живых данных: у трёх выпускников без слотов (4499, 4521, 4523)
начислений не осталось ни за один месяц.

Отсюда последовательность: сначала свод оплаты, потом заморозка долга, и только
затем снятие с расписания.

**Что считается долгом** (решение оператора 2026-08-25): остаток по ВСЕМ
открытым месяцам, приложенный чек долг гасит. Не «просрочка» по правилу школы:
человек уходит, следующего счёта ему никто не выставит, поэтому незакрытый
текущий месяц и есть финальный счёт. Шумом это не станет — проверка живёт в
моменте перевода, а не в ежедневном обходе всех учеников.

**Что закрывается** (решение оператора 2026-08-25): только работа в курсе —
начать попытку и отправить ответ. Материалы остаются на чтение. Причина не в
мягкости: у «Выпускника» нет ни ИИ-наставника, ни выхода на преподавателя, то
есть сданное им задание с ручной проверкой просто повиснет — проверять его
некому. Честный отказ лучше принятого в никуда ответа.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import charge_service, inbox_service, payment_service
from app.utils.exceptions import DomainError

logger = logging.getLogger(__name__)

__all__ = [
    "ALUMNI_PLAN_CODE",
    "COURSE_WORK_CLOSED_CODE",
    "ESCALATION_KIND",
    "ChargeLine",
    "Settlement",
    "SchedulePlan",
    "GraduationPreview",
    "GraduationResult",
    "settlement",
    "schedule_plan",
    "preview",
    "apply",
    "assert_course_work_allowed",
]

#: Код тарифа «Выпускник». Единственная константа-код в модуле: побочные
#: действия вешаются на переход ИМЕННО в него, а права закрываются признаком
#: `subscription_plan.course_work`, а не этим кодом.
ALUMNI_PLAN_CODE = "alumni"

#: Машинный признак отказа в теле ответа — по образцу `payment_overdue`
#: (tsk-617). Клиент отличает «курс завершён» от «сломалось» по коду, а не по
#: словам: формулировка продуктовая и будет меняться.
COURSE_WORK_CLOSED_CODE = "course_work_closed"

#: Вид записи в журнале уведомлений для эскалации о долге выпускника.
ESCALATION_KIND = "alumni_debt"

#: Статусы будущих занятий, которые снимаются вместе с расписанием.
#: `scheduled` — назначено автоматикой, ученик его не подтверждал.
#: `on_break` — то же самое, но погашено перерывом; без него выпускник ВЕРНУЛСЯ
#: БЫ в расписание сам: `break_service` по окончании перерыва переводит такие
#: строки обратно в `scheduled`, не глядя на то, что привязка к слоту погашена
#: (на проде это ровно случай ученика 4500 — два занятия 26.08 и 31.08).
#: Всё, что ученик решил сам (`confirmed`, `declined`, `rescheduled`, `no_show`),
#: остаётся: это его история и уже отмеченная явка.
DETACHABLE_STATUSES = ("scheduled", "on_break")

#: Дни недели для человека — слот в сводке должен читаться, а не расшифровываться.
_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


@dataclass(frozen=True)
class ChargeLine:
    """Один открытый месяц в своде: сколько начислено, пришло и осталось."""

    period: date
    group_id: int
    group_name: str
    charged_minor: int
    paid_minor: int
    pending_minor: int
    #: Непокрытый остаток. Ноль, если приложенный чек его перекрывает.
    due_minor: int


@dataclass(frozen=True)
class Settlement:
    """Свод оплаты на момент перевода — то, что маркетолог видит и решает."""

    lines: list[ChargeLine] = field(default_factory=list)
    charged_minor: int = 0
    paid_minor: int = 0
    pending_minor: int = 0
    #: Итоговый долг. Только он включает эскалацию.
    due_minor: int = 0

    @property
    def has_debt(self) -> bool:
        return self.due_minor > 0


@dataclass(frozen=True)
class SlotLine:
    """Активная привязка к слоту — чтобы в сводке было видно, что именно снимут."""

    slot_id: int
    weekday: int
    start_time: str
    teacher_name: Optional[str]

    @property
    def label(self) -> str:
        return f"{_WEEKDAYS[self.weekday]} {self.start_time}"


@dataclass(frozen=True)
class SchedulePlan:
    """След ученика в расписании: что снимется при переводе."""

    slots: list[SlotLine] = field(default_factory=list)
    future_lessons: int = 0
    #: Будущие занятия, которые останутся: ученик отметил их сам.
    kept_lessons: int = 0


@dataclass(frozen=True)
class GraduationPreview:
    """Что произойдёт при переводе. Показывается ДО нажатия."""

    student_id: int
    schedule: SchedulePlan
    settlement: Settlement


@dataclass(frozen=True)
class GraduationResult:
    """Что произошло при переводе. Возвращается вместе с новым состоянием тарифа.

    Свод едет в ответ, а не только в журнал уведомлений, намеренно. Урок
    tsk-591/652 этой же школы: механизм работал, а сигнал висел непрочитанным.
    Маркетолог нажимает кнопку сам — момент нажатия и есть то единственное
    место, где сигнал точно будет увиден.
    """

    detached_slots: int
    detached_lessons: int
    frozen_charges: int
    settlement: Settlement
    escalated_to: list[int] = field(default_factory=list)


# ──────────────────────────────── свод оплаты ────────────────────────────────


async def settlement(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> Settlement:
    """Начислено, оплачено и что осталось — по всем ОТКРЫТЫМ месяцам ученика.

    Закрытые месяцы сюда не идут: их сумма уже зафиксирована и спорить с ней
    перевод не должен. Формулы своей здесь нет ни одной — итог месяца считает
    `charge_service.charge_total_minor`, покрытие `payment_service.payment_state`.
    Четвёртая копия правила «ручная сумма побеждает расчётную, чек гасит долг
    только если покрывает остаток» разъехалась бы с бейджем в кабинете ученика
    на первой же правке.
    """
    today = today or date.today()
    rows = (
        await db.execute(
            text(
                """
                SELECT ch.period,
                       ch.group_id,
                       pg.name                  AS group_name,
                       ch.calculated_minor,
                       ch.manual_minor,
                       COALESCE(adj.total, 0)   AS adjustments_minor,
                       COALESCE(pay.paid, 0)    AS paid_minor,
                       COALESCE(pay.pending, 0) AS pending_minor
                  FROM student_monthly_charge ch
                  JOIN pricing_group pg ON pg.id = ch.group_id
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total
                          FROM charge_adjustment a
                         WHERE a.student_id = ch.student_id
                           AND a.group_id = ch.group_id
                           AND a.period = ch.period
                  ) adj ON TRUE
                  LEFT JOIN LATERAL (
                        SELECT sum(p.amount_minor) FILTER (WHERE p.status = 'confirmed')
                                   AS paid,
                               sum(p.amount_minor) FILTER (WHERE p.status = 'pending')
                                   AS pending
                          FROM student_payment p
                         WHERE p.student_id = ch.student_id
                           AND p.group_id = ch.group_id
                           AND p.period = ch.period
                  ) pay ON TRUE
                 WHERE ch.student_id = :s
                   AND ch.status = 'open'
                 ORDER BY ch.period, ch.group_id
                """
            ),
            {"s": student_id},
        )
    ).all()

    lines: list[ChargeLine] = []
    charged = paid = pending = due = 0
    for row in rows:
        total_minor = charge_service.charge_total_minor(
            calculated_minor=row.calculated_minor,
            manual_minor=row.manual_minor,
            adjustments_minor=int(row.adjustments_minor),
        )
        state = payment_service.payment_state(
            total_minor=total_minor,
            paid_minor=int(row.paid_minor),
            pending_minor=int(row.pending_minor),
            period=row.period,
            today=today,
        )
        # Долгом считаем остаток, НЕ закрытый ни деньгами, ни приложенным чеком.
        # `is_unpaid` — то же правило, по которому красится бейдж в кабинете.
        line_due = state.due_minor if state.is_unpaid else 0
        lines.append(
            ChargeLine(
                period=row.period,
                group_id=int(row.group_id),
                group_name=row.group_name,
                charged_minor=total_minor,
                paid_minor=state.paid_minor,
                pending_minor=state.pending_minor,
                due_minor=line_due,
            )
        )
        charged += total_minor
        paid += state.paid_minor
        pending += state.pending_minor
        due += line_due

    return Settlement(
        lines=lines,
        charged_minor=charged,
        paid_minor=paid,
        pending_minor=pending,
        due_minor=due,
    )


# ──────────────────────────────── расписание ─────────────────────────────────


async def schedule_plan(db: AsyncSession, student_id: int) -> SchedulePlan:
    """След ученика в расписании: активные слоты и будущие занятия.

    Считает ровно то, что снимет :func:`apply`, — иначе предпросмотр обещал бы
    одно, а перевод делал другое (урок tsk-597/598: общее условие отбора зовут
    функцией, а не переписывают рядом).
    """
    slots = (
        await db.execute(
            text(
                """
                SELECT ls.id, ls.weekday, to_char(ls.start_time, 'HH24:MI') AS start_time,
                       u.full_name AS teacher_name
                  FROM lesson_slot_student lss
                  JOIN lesson_slot ls ON ls.id = lss.slot_id
                  LEFT JOIN users u ON u.id = ls.teacher_id
                 WHERE lss.student_id = :s AND lss.is_active AND ls.is_active
                 ORDER BY ls.weekday, ls.start_time
                """
            ),
            {"s": student_id},
        )
    ).all()

    counts = (
        await db.execute(
            text(
                """
                SELECT count(*) FILTER (
                           WHERE p.status = ANY(CAST(:detachable AS text[]))
                       ) AS detachable,
                       count(*) FILTER (
                           WHERE NOT (p.status = ANY(CAST(:detachable AS text[])))
                       ) AS kept
                  FROM lesson_occurrence_participant p
                  JOIN lesson_occurrence lo ON lo.id = p.occurrence_id
                 WHERE p.student_id = :s AND lo.scheduled_at >= now()
                """
            ),
            {"s": student_id, "detachable": list(DETACHABLE_STATUSES)},
        )
    ).one()

    return SchedulePlan(
        slots=[
            SlotLine(
                slot_id=int(r.id),
                weekday=int(r.weekday),
                start_time=r.start_time,
                teacher_name=r.teacher_name,
            )
            for r in slots
        ],
        future_lessons=int(counts.detachable),
        kept_lessons=int(counts.kept),
    )


async def _detach_from_schedule(db: AsyncSession, student_id: int) -> tuple[int, int]:
    """Снять ученика со всех слотов и из будущих занятий. Без commit.

    **Пересчёт денег здесь НЕ зовётся, и это не забывчивость.** Обычное
    открепление от слота (`lesson_calendar_service.remove_slot_participant`) его
    зовёт — там ученик остаётся в школе, и месяц обязан посчитаться заново по
    новому расписанию. Здесь человек уходит: пересчёт лишил бы его открытый
    месяц основания и удалил бы строку вместе с долгом (см. модуль). Сумму
    уходящего замораживает :func:`_freeze_charges`, а не переписывает пересчёт.

    Returns:
        Пара «сколько слотов погашено, сколько будущих занятий снято».
    """
    slots = await db.execute(
        text(
            "UPDATE lesson_slot_student SET is_active = false "
            " WHERE student_id = :s AND is_active"
        ),
        {"s": student_id},
    )
    lessons = await db.execute(
        text(
            """
            DELETE FROM lesson_occurrence_participant p
                  USING lesson_occurrence lo
                  WHERE p.occurrence_id = lo.id
                    AND p.student_id = :s
                    AND lo.scheduled_at >= now()
                    AND p.status = ANY(CAST(:detachable AS text[]))
            """
        ),
        {"s": student_id, "detachable": list(DETACHABLE_STATUSES)},
    )
    return slots.rowcount, lessons.rowcount


async def _freeze_charges(
    db: AsyncSession, student_id: int, *, closed_by: Optional[int]
) -> int:
    """Закрыть открытые месяцы уходящего ученика, чтобы долг не испарился.

    Закрытый месяц пересчёт не трогает вовсе — это durable-инвариант денежного
    контура (`charge_service.recalculate_student_group`), и здесь он работает
    ровно как задумано: сумма, названная человеку, замирает. Без этого
    ежедневный тик удалил бы строку в ту же ночь, потому что у выпускника нет
    ни расписания, ни тарифной группы, — и «сколько он остался должен» перестало
    бы существовать как факт.

    Суммы не меняются: закрытие переставляет только статус. Итог месяца по школе
    от этого не сдвигается ни на копейку.

    Отличается от `charge_service.close_month` адресатом: тот закрывает месяц
    ВСЕЙ школе разом (действие маркетолога раз в месяц), здесь — строки одного
    уходящего человека.
    """
    res = await db.execute(
        text(
            "UPDATE student_monthly_charge "
            "   SET status = 'closed', closed_at = now(), closed_by = :by, "
            "       updated_at = now() "
            " WHERE student_id = :s AND status = 'open'"
        ),
        {"s": student_id, "by": closed_by},
    )
    return res.rowcount


# ──────────────────────────────── эскалация ──────────────────────────────────


def _amount(minor: int) -> str:
    """Сумма человеку: копейки показываем, только если они есть."""
    if minor % 100 == 0:
        return f"{minor // 100} ₽"
    return f"{minor / 100:.2f} ₽"


async def _notify_marketers(
    db: AsyncSession,
    *,
    student_id: int,
    full_name: Optional[str],
    debt: Settlement,
    created_by: Optional[int],
) -> list[int]:
    """Положить эскалацию о долге в кабинет каждому маркетологу и админу.

    Админ в адресатах не для симметрии: маркетолог в школе один, и без второго
    адресата его отпуск означал бы, что сигнал не увидит никто.

    Уходящего не тревожим — письмо о долге ему шлёт свой механизм (tsk-010),
    и второе сообщение об одном и том же долге выглядело бы как второй долг.
    """
    ids = [
        int(r.user_id)
        for r in (
            await db.execute(
                text(
                    "SELECT DISTINCT ur.user_id "
                    "  FROM user_roles ur "
                    "  JOIN roles r ON r.id = ur.role_id "
                    "  JOIN users u ON u.id = ur.user_id "
                    " WHERE r.name IN ('marketer', 'admin') "
                    "   AND u.is_active AND u.blocked_at IS NULL"
                )
            )
        ).all()
    ]
    if not ids:
        logger.warning(
            "tsk-673: долг выпускника %s (%s коп.) некому эскалировать — "
            "ни маркетологов, ни админов в базе нет",
            student_id, debt.due_minor,
        )
        return []

    who = full_name or f"#{student_id}"
    months = ", ".join(f"{line.period:%m.%Y}" for line in debt.lines if line.due_minor)
    content = (
        f"{who} переведён на тариф «Выпускник», но за ним остался долг "
        f"{_amount(debt.due_minor)} ({months}).\n\n"
        f"Начислено {_amount(debt.charged_minor)}, оплачено "
        f"{_amount(debt.paid_minor)}"
        + (
            f", ждёт разбора {_amount(debt.pending_minor)}"
            if debt.pending_minor
            else ""
        )
        + ".\n\nСумма месяца зафиксирована: пересчёт её больше не изменит. "
        "Если деньги на самом деле пришли — отметьте оплату в разделе «Платежи»."
    )
    payload = {
        "student_id": student_id,
        "due_minor": debt.due_minor,
        "charged_minor": debt.charged_minor,
        "paid_minor": debt.paid_minor,
        "pending_minor": debt.pending_minor,
        "periods": [
            line.period.isoformat() for line in debt.lines if line.due_minor
        ],
    }
    for uid in ids:
        await inbox_service.create_for_user(
            db,
            user_id=uid,
            kind=ESCALATION_KIND,
            title="Выпускник ушёл с долгом",
            content=content,
            payload=payload,
            created_by=created_by,
        )
    logger.info(
        "tsk-673: долг выпускника %s (%s коп.) эскалирован %s адресатам",
        student_id, debt.due_minor, len(ids),
    )
    return ids


# ──────────────────────────────── сценарий ───────────────────────────────────


async def preview(
    db: AsyncSession, student_id: int, *, today: Optional[date] = None
) -> GraduationPreview:
    """Что произойдёт при переводе — до нажатия и без единой записи."""
    return GraduationPreview(
        student_id=student_id,
        schedule=await schedule_plan(db, student_id),
        settlement=await settlement(db, student_id, today=today),
    )


async def apply(
    db: AsyncSession,
    student_id: int,
    *,
    changed_by: Optional[int],
    today: Optional[date] = None,
) -> GraduationResult:
    """Побочные действия перевода на «Выпускника». Тариф уже сменён вызывающим.

    Порядок обязателен и объяснён в шапке модуля: свод → заморозка долга →
    снятие с расписания. Обратный порядок стирает долг вместе со строкой месяца.

    Не коммитит: перевод, снятие и эскалация обязаны быть одним целым. Сбой
    посреди оставил бы человека без тарифа, но в расписании, — а заметно это
    стало бы по жалобе преподавателя.
    """
    debt = await settlement(db, student_id, today=today)

    frozen = 0
    if debt.has_debt:
        frozen = await _freeze_charges(db, student_id, closed_by=changed_by)

    slots, lessons = await _detach_from_schedule(db, student_id)

    escalated: list[int] = []
    if debt.has_debt:
        full_name = await db.scalar(
            text("SELECT full_name FROM users WHERE id = :id"), {"id": student_id}
        )
        escalated = await _notify_marketers(
            db,
            student_id=student_id,
            full_name=full_name,
            debt=debt,
            created_by=changed_by,
        )

    logger.info(
        "tsk-673: выпуск ученика %s — слотов снято %s, будущих занятий %s, "
        "месяцев заморожено %s, долг %s коп.",
        student_id, slots, lessons, frozen, debt.due_minor,
    )
    return GraduationResult(
        detached_slots=slots,
        detached_lessons=lessons,
        frozen_charges=frozen,
        settlement=debt,
        escalated_to=escalated,
    )


# ───────────────────────────── гейт работы в курсе ───────────────────────────


async def assert_course_work_allowed(db: AsyncSession, student_id: int) -> None:
    """Закрыть работу в курсе, если тариф её не даёт.

    Стоит рядом с `payment_access_service.assert_content_allowed` и по тому же
    образцу, но закрывает УЖЕ существующий доступ: тот запрещает и материалы, и
    задания за долг, этот — только начать попытку и отправить ответ. Материалы
    выпускнику остаются: он их читает, а новых ответов у него не принимают
    (решение оператора 2026-08-25).

    Признак берётся из тарифа (`subscription_plan.course_work`), а не из его
    кода: список кодов в сервисе разъехался бы со справочником при первом же
    новом тарифе-архиве (урок tsk-610).

    Тариф не назначен — пропускаем. Отсутствие подписки означает «ещё не
    размечен», а не «выпускник», и трактовать его как запрет значило бы
    закрыть задания всем, кому тариф просто не успели поставить.
    """
    row = (
        await db.execute(
            text(
                "SELECT p.code, p.name, p.course_work "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id = :sid AND s.ends_on IS NULL"
            ),
            {"sid": student_id},
        )
    ).first()
    if row is None or row.course_work:
        return

    logger.info(
        "tsk-673: работа в курсе закрыта ученику %s — тариф %s", student_id, row.code
    )
    raise DomainError(
        detail=(
            f"Обучение по курсу завершено (тариф «{row.name}»). Материалы "
            "остаются открытыми, новые ответы не принимаются."
        ),
        status_code=403,
        payload={"code": COURSE_WORK_CLOSED_CODE, "plan_code": row.code},
    )
