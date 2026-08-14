# app/services/charge_cron_service.py
"""tsk-596: месяц начислений создаётся сам + страж «ходит, но не выставлен».

**Зачем.** До этой задачи строка `student_monthly_charge` появлялась только
двумя путями: как побочный эффект правки расписания ученика
(`charge_service.recalculate_open_months_for_student`) и вручную из кабинета
маркетолога (`POST /charges/recalculate`). Ни одного фонового задания про
деньги в приложении не было. На проде 2026-08-08 в таблице лежал ровно один
период — август 2026, заведённый руками 01.08–03.08. То есть **1 сентября не
выставилось бы ничего**, и узнали бы об этом только когда кто-то заметил.

**Почему проход ежедневный, а не «первого числа».** Тик первого числа
пропускает месяц целиком, если сервер именно в этот день лежал или
перезапускался, а ученик, зачисленный десятого, ждал бы следующего месяца.
Ежедневный проход даёт то же самое, что уже делает правка расписания, только не
зависит от того, тронул ли кто-нибудь расписание.

**Почему это безопасно для денег** (решение оператора 2026-08-08, вариант А):
пересчёт идемпотентен по построению `charge_service`, и ежедневный повтор не
может ни задвоить, ни переписать договорённость:

* вставка идёт `ON CONFLICT (student_id, group_id, period) DO NOTHING`;
* **закрытый месяц не трогается вовсе** — расхождение уходит поправкой в
  следующий открытый месяц (durable-инвариант, `project_lms_monthly_charges`);
* `manual_minor` (сумма, поставленная человеком) пересчётом не стирается —
  меняется только расчётная часть.

**Страж.** Ученик, который ходит на занятия и при этом не выставлен к оплате, —
аномалия, которую никто не видит: пустая строка ничем себя не проявляет.
Детектор ищет таких раз в сутки и пишет методисту, по образцу инвариант-детектора
битых ссылок (tsk-521). Он же ловит случай «ходит, не будучи привязанным ни к
одному курсу» (прод, Терехов 4510) — тот попадает в находки как «нет тарифной
группы».

**Молчание = всё хорошо.** Уведомление уходит только при находках и не чаще
раза в сутки (`CHARGE_ANOMALY_NOTIFY_COOLDOWN_HOURS`).

Порядок внутри тика важен: сначала пересчёт, потом детектор. Наоборот детектор
ругался бы на то, что пересчёт починит через секунду.

**Multi-worker safety — лок берётся ДВАЖДЫ, и это не перестраховка.**
На проде приложение крутится несколькими worker'ами, проход заведён в каждом.
Транзакционный advisory-lock, взятый один раз на входе, здесь работает не так,
как у соседних тиков: `charge_service.recalculate_month` **коммитит внутри
себя**, а коммит транзакционный лок освобождает (проверено на живой базе, см.
`reviews/2026-08-08-tsk596-review-gate.md`, находка Б-1). Сессионный лок той же
проблемы не решает: после коммита SQLAlchemy отдаёт соединение в пул и берёт
следующее, а лок остаётся на прежнем.

Поэтому защита разделена по тому, чем именно опасен двойной заход:

* **пересчёт** двойного захода не боится сам по себе — вставка идёт
  `ON CONFLICT DO NOTHING`, обновление идемпотентно, гонка ловится
  `IntegrityError` внутри `charge_service`. Лишняя работа, не порча денег;
* **уведомление** боится: оба worker'а прочли бы «сегодня не отправляли» и
  написали методисту одно и то же. Эта фаза — проверка отсрочки, вставка,
  коммит — целиком укрыта локом, который живёт ровно до её конца.

Лок на входе оставлен: в обычном случае он отсекает второй проход до всякой
работы, и только после первого коммита пересчёта окно приоткрывается.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import async_session_factory
from app.services import charge_service, inbox_service

logger = logging.getLogger("app.charge_cron")

# Ключ advisory-lock. Не должен пересекаться с соседними тиками: escalation —
# 0x59365453 ("Y6TS"), проверка ссылок — 0x4C494E4B ("LINK").
_CHARGE_CRON_LOCK_KEY = 0x43485247  # ascii "CHRG"

NOTIFICATION_KIND = "unbilled_active_students"

#: Служебные роли: их носители появляются в расписании как ведущие занятия, а не
#: как плательщики. Виктор Комлев (id 2) числится и `teacher`, и `student` — без
#: этого отсева он попадал бы в находки каждый день.
_STAFF_ROLES = ("teacher", "admin", "methodist", "marketer")

_scheduler: Optional[AsyncIOScheduler] = None


# --------------------------------------------------------------------- детектор


#: Кто считается «ходит»: активная привязка к активному слоту ЛИБО занятие в
#: будущем. Второе условие нужно отдельно: разовое занятие ставится вне
#: постоянного расписания, слота под ним нет вовсе.
#:
#: Фильтр `lss.is_active` обязателен и не дублирует `ls.is_active`: сам слот
#: живёт дальше с другими учениками, а выбывшего от него отвязывают — на проде
#: трое из пяти «невыставленных» оказались именно такими, и по слоту без этого
#: фильтра выглядели как действующие.
#:
#: tsk-610: `subscription_plan.billing_exempt` — тариф, по которому денег не
#: берут ОСОЗНАННО (сейчас `test`). Такой ученик попадал в предупреждение каждый
#: день законно, и список из двух строк, где одна всегда ложная, приучает не
#: открывать уведомление вовсе: три дня подряд оно висело непрочитанным, а рядом
#: с постоянным пунктом стоял настоящий случай. Признак живёт в ДАННЫХ, а не
#: набором кодов в коде: новый «денег не берём» тариф не должен требовать релиза.
#: `demo` намеренно НЕ exempt — ученик на демо с занятиями и есть та самая дыра.
_ANOMALY_SQL = """
WITH staff AS (
    SELECT DISTINCT ur.user_id
      FROM user_roles ur JOIN roles r ON r.id = ur.role_id
     WHERE r.name = ANY(:staff_roles)
),
active_slots AS (
    SELECT lss.student_id, count(*) AS n
      FROM lesson_slot_student lss
      JOIN lesson_slot ls ON ls.id = lss.slot_id
     WHERE lss.is_active AND ls.is_active
     GROUP BY 1
),
future_lessons AS (
    SELECT p.student_id, count(*) AS n
      FROM lesson_occurrence_participant p
      JOIN lesson_occurrence o ON o.id = p.occurrence_id
     WHERE o.scheduled_at >= now()
     GROUP BY 1
),
sub AS (
    SELECT s.student_id, s.pricing_group_id, sp.code AS plan_code,
           sp.billing_exempt
      FROM student_subscription s
      JOIN subscription_plan sp ON sp.id = s.plan_id
     WHERE s.ends_on IS NULL
),
paid_groups AS (
    SELECT DISTINCT uc.user_id AS student_id, cp.group_id
      FROM user_courses uc
      JOIN course_pricing cp ON cp.course_id = uc.course_id
                            AND cp.sale_status = 'paid'
     WHERE uc.is_active
)
SELECT u.id                                   AS student_id,
       u.full_name,
       COALESCE(active_slots.n, 0)            AS active_slots,
       COALESCE(future_lessons.n, 0)          AS future_lessons,
       sub.plan_code,
       sub.pricing_group_id                   AS subscription_group_id,
       (sub.student_id IS NOT NULL)           AS has_subscription,
       (SELECT count(*) FROM paid_groups pg WHERE pg.student_id = u.id) AS paid_course_groups
  FROM users u
  LEFT JOIN active_slots   ON active_slots.student_id = u.id
  LEFT JOIN future_lessons ON future_lessons.student_id = u.id
  LEFT JOIN sub            ON sub.student_id = u.id
 WHERE u.is_active
   AND u.id NOT IN (SELECT user_id FROM staff)
   AND NOT COALESCE(sub.billing_exempt, false)
   AND (COALESCE(active_slots.n, 0) > 0 OR COALESCE(future_lessons.n, 0) > 0)
   AND NOT EXISTS (
           SELECT 1 FROM student_monthly_charge ch
            WHERE ch.student_id = u.id AND ch.period = :period
       )
 ORDER BY u.full_name
"""


def _diagnose(row: Any) -> str:
    """Почему у этого ученика нет строки месяца — словами, а не кодом ошибки.

    Причина нужна в самом уведомлении: «нет начисления» одинаково выглядит и
    когда ученику забыли назначить тариф, и когда он на бесплатном плане, и
    когда цену просто некому вывести. Действия при этом РАЗНЫЕ, и методист не
    должен каждый раз лезть в базу, чтобы это выяснить.
    """
    if row.has_subscription and row.subscription_group_id is None:
        return f"подписка «{row.plan_code}» без тарифной группы — денег не берут"
    if not row.has_subscription and int(row.paid_course_groups) == 0:
        return "нет ни подписки, ни платного курса — тарифной группы нет"
    return "тарифная группа есть, но цена не разрешилась (проверить тариф и расписание)"


async def find_unbilled_active_students(
    db: AsyncSession, *, period: date
) -> list[dict]:
    """Кто ходит на занятия, но не выставлен к оплате за `period`.

    Read-only: детектор ничего не чинит и не пишет в деньги. Он только называет
    имя и причину — что делать дальше, решает человек.
    """
    rows = (
        await db.execute(
            text(_ANOMALY_SQL),
            {"period": charge_service.month_start(period), "staff_roles": list(_STAFF_ROLES)},
        )
    ).all()
    return [
        {
            "student_id": int(r.student_id),
            "full_name": r.full_name,
            "active_slots": int(r.active_slots),
            "future_lessons": int(r.future_lessons),
            "plan_code": r.plan_code,
            "reason": _diagnose(r),
        }
        for r in rows
    ]


async def _recently_notified(db: AsyncSession, cooldown_hours: int) -> bool:
    """True, если уведомление такого рода уже отправляли за последние N часов."""
    res = await db.execute(
        text(
            "SELECT count(*) FROM notifications "
            "WHERE kind = :kind "
            "  AND modified_at >= now() - CAST(:h AS text)::interval"
        ),
        {"kind": NOTIFICATION_KIND, "h": f"{int(cooldown_hours)} hours"},
    )
    return int(res.scalar() or 0) > 0


async def _notify_methodists(
    db: AsyncSession, *, findings: list[dict], period: date, max_examples: int
) -> int:
    """Кладёт уведомление о находках каждому методисту. Возвращает их число."""
    res = await db.execute(
        text(
            "SELECT ur.user_id FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id WHERE r.name = 'methodist'"
        )
    )
    methodist_ids = [int(row[0]) for row in res.fetchall()]
    if not methodist_ids:
        logger.warning("tsk-596: методистов в базе нет — уведомлять некого")
        return 0

    examples = findings[:max_examples]
    payload = {
        "period": period.isoformat(),
        "unbilled_count": len(findings),
        "examples": examples,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    lines = [
        f"За {period:%m.%Y} без начисления остались {len(findings)} чел., "
        "хотя занятия у них идут:",
        "",
    ]
    for item in examples:
        lines.append(
            f"• {item['full_name']} (id {item['student_id']}): "
            f"слотов {item['active_slots']}, занятий впереди {item['future_lessons']} "
            f"— {item['reason']}"
        )
    if len(findings) > len(examples):
        lines.append(f"…и ещё {len(findings) - len(examples)}.")
    lines.append("")
    lines.append("Ученик ходит, а счёта ему никто не выставил.")

    for mid in methodist_ids:
        await inbox_service.create_for_user(
            db,
            user_id=mid,
            kind=NOTIFICATION_KIND,
            title="Ученики ходят, но не выставлены к оплате",
            content="\n".join(lines),
            payload=payload,
            created_by=None,
        )
    return len(methodist_ids)


# ------------------------------------------------------------------------- тик


async def charge_cron_tick(
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    *,
    today: Optional[date] = None,
) -> dict:
    """Один проход: пересчёт текущего месяца, затем проверка на аномалии.

    `session_factory` и `today` — точки подмены: в проде APScheduler зовёт тик
    без аргументов, тесты передают свою фабрику и свою дату.

    Возвращает сводку для логов и тестов.
    """
    factory = session_factory or async_session_factory
    settings = Settings()
    period = charge_service.month_start(today or date.today())
    summary: dict = {
        "locked": False,
        "period": period.isoformat(),
        "recalculated": 0,
        "unbilled": 0,
        "notified": 0,
    }

    async with factory() as db:
        if not await _try_lock(db):
            logger.info("tsk-596: тик пропущен — работу делает другой worker")
            return summary
        summary["locked"] = True

        summary["recalculated"] = await charge_service.recalculate_month(db, period=period)
        logger.info(
            "tsk-596: месяц %s пересчитан, затронуто строк: %s",
            period,
            summary["recalculated"],
        )

        findings = await find_unbilled_active_students(db, period=period)
        summary["unbilled"] = len(findings)
        # Список находок в сводке, а не только счётчик: иначе в логах не видно,
        # КТО именно остался невыставленным, а тест проверял бы число вместо
        # конкретного человека.
        summary["unbilled_students"] = findings[:50]

        if not findings:
            logger.info("tsk-596: невыставленных учеников за %s нет", period)
            return summary

        logger.warning(
            "tsk-596: без начисления за %s остались %s чел.: %s",
            period,
            len(findings),
            [f["full_name"] for f in findings[:5]],
        )

        # Фаза уведомления под локом целиком: проверка отсрочки и вставка
        # должны быть неделимы, иначе два worker'а прочтут «сегодня не
        # отправляли» одновременно и напишут методисту одно и то же дважды.
        # Лок держится до коммита в конце этой ветки.
        if not await _try_lock(db):
            logger.info("tsk-596: уведомление отправляет другой worker")
            return summary

        if await _recently_notified(db, settings.charge_anomaly_notify_cooldown_hours):
            logger.info("tsk-596: уведомление уже отправляли недавно — молчим")
            return summary

        summary["notified"] = await _notify_methodists(
            db,
            findings=findings,
            period=period,
            max_examples=settings.charge_anomaly_max_examples,
        )
        await db.commit()

    return summary


async def _try_lock(db: AsyncSession) -> bool:
    """Взять транзакционный advisory-lock прохода. False — занят другим worker'ом.

    Транзакционный, а не сессионный: сессионный пришлось бы снимать руками, а
    упавший проход оставил бы его висеть до конца жизни соединения — и все
    последующие проходы молча отступали бы, считая, что работу делает кто-то
    другой. Транзакционный уходит сам вместе с транзакцией, в том числе при
    падении.
    """
    got = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k) AS locked"),
        {"k": _CHARGE_CRON_LOCK_KEY},
    )
    return bool(got.scalar())


async def _safe_tick() -> None:
    """Обёртка для планировщика: упавший тик не должен ронять остальные задачи.

    Молчать при этом нельзя — след в логе остаётся всегда, иначе отказ
    неотличим от чистого прогона.
    """
    try:
        await charge_cron_tick()
    except Exception:
        logger.exception("tsk-596: тик упал — месяц за этот прогон не пересчитан")


def start_scheduler() -> None:
    """Поднимает суточный проход (если включён настройкой)."""
    global _scheduler
    settings = Settings()
    if not settings.charge_cron_enabled:
        logger.info("tsk-596: автопересчёт начислений выключен (CHARGE_CRON_ENABLED)")
        return
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _safe_tick,
        IntervalTrigger(hours=settings.charge_cron_interval_hours),
        id="charge_cron_tick",
        max_instances=1,
        # Пропущенные прогоны не догоняем пачкой: результат у них одинаковый,
        # а нагрузка тройная.
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "tsk-596: автопересчёт начислений запущен, интервал %s ч",
        settings.charge_cron_interval_hours,
    )


def stop_scheduler() -> None:
    """Останавливает тик при остановке приложения."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
