"""tsk-010 — напоминание о просроченной оплате.

Отправка запускается человеком, а не расписанием: решение оператора. Поэтому
здесь нет планировщика — только «покажи, кому уйдёт» и «отправь».

Два правила, оба про деликатность:

1. **Не чаще раза в неделю на один и тот же долг.** Факт отправки пишется в
   `notifications` (принятый в проекте журнал), и следующий запуск видит его.
   Иначе нажатие кнопки дважды подряд отправило бы человеку два письма.
2. **Тех, кому писать некуда, не проглатываем.** Ученик без почты возвращается
   отдельным списком: маркетолог напомнит ему сам — в мессенджере или звонком.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import charge_service, inbox_service, notification_email_service, payment_service

logger = logging.getLogger(__name__)
settings = Settings()

__all__ = [
    "OverdueDebtor",
    "ReminderRun",
    "list_overdue",
    "send_reminders",
    "REMINDER_KIND",
    "REPEAT_AFTER_DAYS",
]

#: Вид записи в журнале уведомлений. По нему же идёт проверка «уже напоминали».
REMINDER_KIND = "payment_overdue"

#: Повтор напоминания об одном долге — решение оператора: раз в неделю.
REPEAT_AFTER_DAYS = 7


@dataclass
class OverdueDebtor:
    """Один просроченный долг: кому, за что, сколько и куда писать."""

    student_id: int
    full_name: Optional[str]
    email: Optional[str]
    group_id: int
    group_name: str
    period: date
    due_minor: int
    #: Уже напоминали на этой неделе — письмо не отправится.
    reminded_recently: bool
    #: Из чего сложилась сумма (tsk-756). Письмо уходит живому человеку, и
    #: увидеть «611 ₽ у того, у кого занятий не было» нужно ДО отправки, а не
    #: после: 01.09.2026 семь писем из шестнадцати ушли по неверным данным.
    total_minor: int = 0
    paid_minor: int = 0
    expected_lessons: int = 0
    break_lessons: int = 0
    not_started_lessons: int = 0
    missing_lessons: int = 0
    #: Занятий, которые у ученика фактически были в этом месяце.
    fact_lessons: int = 0
    #: Сумма месяца поставлена руками — расчёт её не перебивал.
    is_manual: bool = False

    @property
    def basis(self) -> str:
        """Основание суммы одной строкой — то, что оператор читает перед отправкой."""
        if self.is_manual:
            head = "сумма поставлена руками"
        else:
            billable = max(
                self.expected_lessons
                - self.break_lessons
                - self.not_started_lessons
                - self.missing_lessons,
                0,
            )
            parts = [f"{self.expected_lessons} занятий по сетке"]
            if self.not_started_lessons:
                parts.append(f"{self.not_started_lessons} до прихода")
            if self.break_lessons:
                parts.append(f"{self.break_lessons} в перерыве")
            if self.missing_lessons:
                parts.append(f"{self.missing_lessons} не состоялось")
            parts.append(f"{billable} к оплате")
            head = ", ".join(parts)
        return (
            f"{head}; фактически занятий {self.fact_lessons}; "
            f"начислено {self.total_minor / 100:.2f} ₽, "
            f"оплачено {self.paid_minor / 100:.2f} ₽"
        )


@dataclass
class ReminderRun:
    """Итог запуска: кому ушло, кому не смогли, о ком нужно позаботиться руками."""

    sent: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped_recent: list[str] = field(default_factory=list)
    without_email: list[str] = field(default_factory=list)


async def list_overdue(db: AsyncSession, *, today: Optional[date] = None) -> list[OverdueDebtor]:
    """Кто просрочил оплату на сегодня.

    Просрочку определяет `payment_service.payment_state` — та же логика, что
    красит бейдж в кабинете. Второй копии правила «сколько дней ждём» здесь нет:
    разъехавшись, они дали бы письмо человеку, у которого на экране всё в порядке.
    """
    today = today or date.today()
    rows = (
        await db.execute(
            text(
                """
                SELECT ch.student_id,
                       u.full_name,
                       u.email,
                       ch.group_id,
                       pg.name AS group_name,
                       ch.period,
                       ch.calculated_minor,
                       ch.manual_minor,
                       ch.expected_lessons,
                       ch.break_lessons,
                       ch.not_started_lessons,
                       ch.missing_lessons,
                       -- tsk-756: занятий фактически было. Рядом с расчётом это
                       -- сразу показывает «начислено, а занятий ноль».
                       (SELECT count(*)
                          FROM lesson_occurrence_participant lop
                          JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
                         WHERE lop.student_id = ch.student_id
                           AND date_trunc(
                                 'month',
                                 (lo.scheduled_at AT TIME ZONE 'Europe/Moscow')
                               )::date = ch.period) AS fact_lessons,
                       COALESCE(adj.total, 0)  AS adjustments_minor,
                       COALESCE(pay.paid, 0)    AS paid_minor,
                       COALESCE(pay.pending, 0) AS pending_minor,
                       EXISTS (
                           SELECT 1 FROM notifications n
                            WHERE n.kind = :kind
                              AND n.user_id = ch.student_id
                              AND n.payload->>'period' = ch.period::text
                              -- Окно повтора считается от НАСТОЯЩЕГО времени:
                              -- журнал пишется в реальном времени, и сравнивать
                              -- его с подставной датой значило бы сравнивать
                              -- разные шкалы. Параметр `today` отвечает только
                              -- за просрочку.
                              -- Умножение на interval, а не CAST строки: asyncpg
                              -- ждёт для interval объект timedelta и на строке
                              -- «7 days» падает с DataError.
                              AND n.modified_at >= now() - (:window_days * interval '1 day')
                       ) AS reminded_recently
                  FROM student_monthly_charge ch
                  JOIN users u ON u.id = ch.student_id
                  JOIN pricing_group pg ON pg.id = ch.group_id
                  LEFT JOIN LATERAL (
                        SELECT sum(a.amount_minor) AS total
                          FROM charge_adjustment a
                         WHERE a.student_id = ch.student_id
                           AND a.group_id = ch.group_id
                           AND a.period = ch.period
                  ) adj ON TRUE
                  LEFT JOIN LATERAL (
                        SELECT sum(p.amount_minor) FILTER (WHERE p.status = 'confirmed') AS paid,
                               sum(p.amount_minor) FILTER (WHERE p.status = 'pending')   AS pending
                          FROM student_payment p
                         WHERE p.student_id = ch.student_id
                           AND p.group_id = ch.group_id
                           AND p.period = ch.period
                  ) pay ON TRUE
                 -- Слитые и заблокированные учётки не тревожим: за ними уже нет
                 -- живого человека, которому это письмо адресовано.
                 WHERE ch.status = 'open'
                   AND u.is_active
                   AND u.blocked_at IS NULL
                 ORDER BY u.full_name, ch.period
                """
            ),
            {"kind": REMINDER_KIND, "window_days": REPEAT_AFTER_DAYS},
        )
    ).all()

    debtors: list[OverdueDebtor] = []
    for r in rows:
        total_minor = charge_service.charge_total_minor(
            calculated_minor=r.calculated_minor,
            manual_minor=r.manual_minor,
            adjustments_minor=int(r.adjustments_minor),
        )
        state = payment_service.payment_state(
            total_minor=total_minor,
            paid_minor=int(r.paid_minor),
            pending_minor=int(r.pending_minor),
            period=r.period,
            today=today,
        )
        if not state.is_overdue:
            continue
        debtors.append(
            OverdueDebtor(
                student_id=r.student_id,
                full_name=r.full_name,
                email=(r.email or "").strip() or None,
                group_id=r.group_id,
                group_name=r.group_name,
                period=r.period,
                due_minor=state.due_minor,
                reminded_recently=bool(r.reminded_recently),
                total_minor=total_minor,
                paid_minor=int(r.paid_minor),
                expected_lessons=int(r.expected_lessons or 0),
                break_lessons=int(r.break_lessons or 0),
                not_started_lessons=int(r.not_started_lessons or 0),
                missing_lessons=int(r.missing_lessons or 0),
                fact_lessons=int(r.fact_lessons or 0),
                is_manual=r.manual_minor is not None,
            )
        )
    return debtors


async def send_reminders(
    db: AsyncSession, *, sent_by: int, today: Optional[date] = None
) -> ReminderRun:
    """Отправить напоминания тем, кому ещё не писали на этой неделе.

    Запись в журнал делается ТОЛЬКО после успешной отправки: иначе сбой почты
    закрыл бы человеку напоминание на неделю вперёд, и он бы его не получил
    вовсе.
    """
    run = ReminderRun()
    for debtor in await list_overdue(db, today=today):
        who = debtor.full_name or f"#{debtor.student_id}"
        if debtor.email is None:
            run.without_email.append(who)
            continue
        if debtor.reminded_recently:
            run.skipped_recent.append(who)
            continue

        ok = await notification_email_service.send_payment_overdue(
            recipient_email=debtor.email,
            full_name=debtor.full_name,
            period=debtor.period,
            group_name=debtor.group_name,
            due_minor=debtor.due_minor,
            settings=settings,
        )
        if not ok:
            run.failed.append(who)
            logger.warning(
                "tsk-010: не удалось отправить напоминание ученику %s за %s",
                debtor.student_id,
                debtor.period,
            )
            continue

        await inbox_service.create_for_user(
            db,
            user_id=debtor.student_id,
            kind=REMINDER_KIND,
            title="Не оплачено обучение",
            content=(
                f"За {debtor.period:%m.%Y} осталось оплатить "
                f"{debtor.due_minor / 100:.2f} ₽."
            ),
            payload={
                "period": debtor.period.isoformat(),
                "group_id": debtor.group_id,
                "due_minor": debtor.due_minor,
            },
            created_by=sent_by,
        )
        run.sent.append(who)

    await db.commit()
    logger.info(
        "tsk-010: напоминания о просрочке — отправлено %s, не дошло %s, "
        "пропущено (уже писали) %s, без почты %s",
        len(run.sent),
        len(run.failed),
        len(run.skipped_recent),
        len(run.without_email),
    )
    return run
