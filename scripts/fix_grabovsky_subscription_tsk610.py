"""tsk-610: вернуть Грабовскому платёжную принадлежность и выставить август (прод).

**Что произошло.** Ученик уже был в базе — 4525 «Владимир Грабовский», тариф
`base_legacy` с тарифной группой 1 (старая цена 5 500 ₽ при двух занятиях в
неделю). 10.08 он зарегистрировался сам, получил учётку 4560, и автослияние
дублей при регистрации (tsk-455) увело на неё расписание, курсы, занятия и почту.
Подписка и перерыв в списках переноса не значились — обе таблицы появились позже
самих списков, — поэтому на живой учётке остался `demo` без тарифной группы.
`billing_group_ids` для такого возвращает пусто: ученик ходил на занятия и был
невидим для начислений, а страж (tsk-596) три дня подряд писал об этом в
непрочитанное уведомление.

**Решение оператора 2026-08-14** (разбор — `reviews/2026-08-14-tsk610-grabovsky.md`):

* тариф — **группа 1, «2 раза в неделю», 5 500 ₽**: у ученика ровно две
  действующие привязки к слотам, сумма выходит сама, руками её ставить не нужно;
* дата начала — **08.08**, как у подписки на слитой учётке: переносим ту самую
  строку, которую должно было перенести слияние, а не заводим новую «сегодня»;
* август — **полный, 5 500 ₽**. Перерыв «Отъезд» 22.07–04.08 остаётся на слитой
  учётке 4525 и на деньги не влияет — это осознанный выбор оператора, а не
  недосмотр. Если перерыв когда-нибудь перенести, открытый месяц пересчитается
  сам и станет 4 400 ₽ (10 занятий по расписанию, 2 из них в перерыве).

Сумма НЕ проставляется руками (`manual_minor` не трогается): она должна
получиться расчётом `charge_service`, и это же служит проверкой, что механизм
работает. Скрипт только возвращает подписку на место и зовёт штатный пересчёт.

Протокол (`/db-check`, режим записи):
    python scripts/fix_grabovsky_subscription_tsk610.py            # сухой прогон
    DBCHECK_OK=1 python scripts/fix_grabovsky_subscription_tsk610.py --apply

Запускать на сервере под `app` (R-009 operator-runbook):
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \\
        scripts/fix_grabovsky_subscription_tsk610.py"'

Проверка после записи — поштучная, отдельной сессией, по каждой строке
отдельно, а не агрегатом (урок `feedback_backfill_verify_every_row`).

Одноразовый скрипт: durable-часть (слияние переносит подписку и перерыв, страж
молчит про тарифы «денег не берут») живёт в `user_merge_service` и
`charge_cron_service`, а не здесь.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402
from app.services import charge_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tsk610.fix")

PERIOD = date(2026, 8, 1)

SOURCE_ID = 4525  # слитая учётка, на ней остался тариф
TARGET_ID = 4560  # живая учётка, на ней расписание и занятия
SOURCE_NAME = "Владимир Грабовский"
TARGET_NAME = "Грабовский Владимир Антонович"

EXPECTED_PLAN = "base_legacy"
EXPECTED_GROUP = 1
EXPECTED_STARTS_ON = date(2026, 8, 8)
DEMO_PLAN = "demo"
#: Демо закрывается днём своей выдачи: она действовала ноль дней и выдана
#: ошибочно — человек уже был учеником, просто на другой учётке.
DEMO_ENDS_ON = date(2026, 8, 10)
EXPECTED_CALCULATED_MINOR = 550_000
EXPECTED_LESSONS = 10
EXPECTED_BREAK_LESSONS = 0

REASON_SUFFIX = "; tsk-610: перенос со слитой учётки 4525 (слияние подписку не переносило)"


async def _preflight(db) -> bool:
    """Сверить состояние прода с тем, что оператор принимал глазами.

    Любое расхождение — стоп до записи: за время между разбором и запуском
    данные мог тронуть кто угодно, включая суточный проход начислений.
    """
    ok = True

    users = {
        int(r.id): r
        for r in (
            await db.execute(
                text(
                    "SELECT id, full_name, is_active, merged_into_user_id "
                    "  FROM users WHERE id IN (:s, :t)"
                ),
                {"s": SOURCE_ID, "t": TARGET_ID},
            )
        ).all()
    }
    for uid, name in ((SOURCE_ID, SOURCE_NAME), (TARGET_ID, TARGET_NAME)):
        row = users.get(uid)
        if row is None:
            logger.error("СТОП: пользователь %s не найден", uid)
            ok = False
        elif row.full_name != name:
            logger.error(
                "СТОП: у %s ФИО «%s», ожидалось «%s»", uid, row.full_name, name
            )
            ok = False

    source = users.get(SOURCE_ID)
    if source is not None and source.merged_into_user_id != TARGET_ID:
        logger.error(
            "СТОП: %s не слита в %s (merged_into_user_id=%s)",
            SOURCE_ID, TARGET_ID, source.merged_into_user_id,
        )
        ok = False
    target = users.get(TARGET_ID)
    if target is not None and not target.is_active:
        logger.error("СТОП: живая учётка %s неактивна", TARGET_ID)
        ok = False

    subs = (
        await db.execute(
            text(
                "SELECT s.id, s.student_id, p.code, s.pricing_group_id, s.starts_on "
                "  FROM student_subscription s "
                "  JOIN subscription_plan p ON p.id = s.plan_id "
                " WHERE s.student_id IN (:s, :t) AND s.ends_on IS NULL "
                " ORDER BY s.student_id"
            ),
            {"s": SOURCE_ID, "t": TARGET_ID},
        )
    ).all()
    source_sub = next((r for r in subs if r.student_id == SOURCE_ID), None)
    target_sub = next((r for r in subs if r.student_id == TARGET_ID), None)

    if source_sub is None:
        logger.error("СТОП: у слитой %s нет действующей подписки — переносить нечего", SOURCE_ID)
        ok = False
    else:
        if source_sub.code != EXPECTED_PLAN or source_sub.pricing_group_id != EXPECTED_GROUP:
            logger.error(
                "СТОП: у %s подписка «%s»/группа %s, ожидалось «%s»/группа %s",
                SOURCE_ID, source_sub.code, source_sub.pricing_group_id,
                EXPECTED_PLAN, EXPECTED_GROUP,
            )
            ok = False
        if source_sub.starts_on != EXPECTED_STARTS_ON:
            logger.error(
                "СТОП: подписка %s начинается %s, ожидалось %s",
                source_sub.id, source_sub.starts_on, EXPECTED_STARTS_ON,
            )
            ok = False

    if target_sub is None:
        logger.error("СТОП: у живой %s нет действующей подписки — состояние изменилось", TARGET_ID)
        ok = False
    elif target_sub.code != DEMO_PLAN:
        logger.error(
            "СТОП: у живой %s тариф «%s», а не «%s» — кто-то уже правил",
            TARGET_ID, target_sub.code, DEMO_PLAN,
        )
        ok = False

    charges = (
        await db.execute(
            text(
                "SELECT id, group_id, calculated_minor, manual_minor, status "
                "  FROM student_monthly_charge "
                " WHERE student_id = :t AND period = :p"
            ),
            {"t": TARGET_ID, "p": PERIOD},
        )
    ).all()
    if charges:
        logger.error(
            "СТОП: у %s уже есть начисление за %s (%s строк) — разбирать руками",
            TARGET_ID, PERIOD, len(charges),
        )
        ok = False

    payments = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_payment WHERE student_id IN (:s, :t)"
            ),
            {"s": SOURCE_ID, "t": TARGET_ID},
        )
    ).scalar_one()
    if payments:
        logger.error("СТОП: за учётками числятся платежи (%s) — деньги молча не трогаем", payments)
        ok = False

    counts = await charge_service.lesson_counts_for_month(
        db, student_id=TARGET_ID, period=PERIOD
    )
    logger.info(
        "Расписание августа у %s: занятий по слотам %s, из них в перерыве %s",
        TARGET_ID, counts.expected, counts.on_break,
    )
    if counts.expected != EXPECTED_LESSONS or counts.on_break != EXPECTED_BREAK_LESSONS:
        logger.error(
            "СТОП: ожидалось %s занятий и %s в перерыве — расписание или перерыв изменились",
            EXPECTED_LESSONS, EXPECTED_BREAK_LESSONS,
        )
        ok = False

    logger.info("")
    logger.info("ПЛАН ЗАПИСИ:")
    logger.info(
        "  1. Закрыть подписку %s («%s» у %s): ends_on = %s",
        getattr(target_sub, "id", "?"), DEMO_PLAN, TARGET_ID, DEMO_ENDS_ON,
    )
    logger.info(
        "  2. Перенести подписку %s («%s», группа %s, с %s) на ученика %s",
        getattr(source_sub, "id", "?"), EXPECTED_PLAN, EXPECTED_GROUP,
        EXPECTED_STARTS_ON, TARGET_ID,
    )
    logger.info("  3. Пересчёт августа штатным charge_service (сумма не ставится руками)")
    logger.info(
        "  Ожидаемый итог: %s ₽ (расчёт %s коп., перерыв на деньги не влияет)",
        EXPECTED_CALCULATED_MINOR // 100, EXPECTED_CALCULATED_MINOR,
    )
    logger.info("")
    return ok


async def _apply(db) -> None:
    """Записать: закрыть demo, перенести подписку, позвать штатный пересчёт."""
    closed = await db.execute(
        text(
            "UPDATE student_subscription SET ends_on = :d "
            " WHERE student_id = :t AND ends_on IS NULL"
        ),
        {"d": DEMO_ENDS_ON, "t": TARGET_ID},
    )
    logger.info("Закрыто действующих подписок у %s: %s", TARGET_ID, closed.rowcount)

    moved = await db.execute(
        text(
            "UPDATE student_subscription "
            "   SET student_id = :t, changed_by = 2, "
            "       reason = COALESCE(reason, '') || :suffix "
            " WHERE student_id = :s"
        ),
        {"t": TARGET_ID, "s": SOURCE_ID, "suffix": REASON_SUFFIX},
    )
    logger.info("Перенесено подписок %s → %s: %s", SOURCE_ID, TARGET_ID, moved.rowcount)

    # Пересчёт коммитит сам — сумма приходит из кода сервиса, а не из SQL скрипта.
    await charge_service.recalculate_for_student(db, student_id=TARGET_ID, period=PERIOD)
    logger.info("Пересчёт августа выполнен")


async def _verify() -> bool:
    """Проверка ОТДЕЛЬНОЙ сессией и построчно: что реально легло в базу."""
    ok = True
    async with async_session_factory() as db:
        sub = (
            await db.execute(
                text(
                    "SELECT s.id, p.code, s.pricing_group_id, s.starts_on "
                    "  FROM student_subscription s "
                    "  JOIN subscription_plan p ON p.id = s.plan_id "
                    " WHERE s.student_id = :t AND s.ends_on IS NULL"
                ),
                {"t": TARGET_ID},
            )
        ).all()
        if len(sub) != 1:
            logger.error("ПРОВАЛ: действующих подписок у %s — %s, ждали одну", TARGET_ID, len(sub))
            ok = False
        else:
            row = sub[0]
            if (row.code, row.pricing_group_id, row.starts_on) != (
                EXPECTED_PLAN, EXPECTED_GROUP, EXPECTED_STARTS_ON
            ):
                logger.error(
                    "ПРОВАЛ: подписка «%s»/группа %s/с %s, ждали «%s»/%s/%s",
                    row.code, row.pricing_group_id, row.starts_on,
                    EXPECTED_PLAN, EXPECTED_GROUP, EXPECTED_STARTS_ON,
                )
                ok = False
            else:
                logger.info(
                    "OK: тариф %s — «%s», группа %s, с %s",
                    TARGET_ID, row.code, row.pricing_group_id, row.starts_on,
                )

        left = (
            await db.execute(
                text("SELECT count(*) FROM student_subscription WHERE student_id = :s"),
                {"s": SOURCE_ID},
            )
        ).scalar_one()
        if left:
            logger.error("ПРОВАЛ: на слитой %s осталось подписок: %s", SOURCE_ID, left)
            ok = False
        else:
            logger.info("OK: на слитой %s подписок не осталось", SOURCE_ID)

        demo = (
            await db.execute(
                text(
                    "SELECT s.id, s.ends_on FROM student_subscription s "
                    "  JOIN subscription_plan p ON p.id = s.plan_id "
                    " WHERE s.student_id = :t AND p.code = :c"
                ),
                {"t": TARGET_ID, "c": DEMO_PLAN},
            )
        ).all()
        for row in demo:
            if row.ends_on != DEMO_ENDS_ON:
                logger.error(
                    "ПРОВАЛ: demo %s закрыта %s, ждали %s", row.id, row.ends_on, DEMO_ENDS_ON
                )
                ok = False
            else:
                logger.info("OK: demo %s закрыта %s", row.id, row.ends_on)

        charges = (
            await db.execute(
                text(
                    "SELECT id, group_id, calculated_minor, manual_minor, "
                    "       expected_lessons, break_lessons, status "
                    "  FROM student_monthly_charge "
                    " WHERE student_id = :t AND period = :p"
                ),
                {"t": TARGET_ID, "p": PERIOD},
            )
        ).all()
        if len(charges) != 1:
            logger.error("ПРОВАЛ: строк начисления за %s — %s, ждали одну", PERIOD, len(charges))
            ok = False
        else:
            ch = charges[0]
            expected = (
                EXPECTED_GROUP, EXPECTED_CALCULATED_MINOR, None,
                EXPECTED_LESSONS, EXPECTED_BREAK_LESSONS, "open",
            )
            actual = (
                ch.group_id, ch.calculated_minor, ch.manual_minor,
                ch.expected_lessons, ch.break_lessons, ch.status,
            )
            if actual != expected:
                logger.error("ПРОВАЛ: начисление %s, ждали %s", actual, expected)
                ok = False
            else:
                logger.info(
                    "OK: август %s — %s ₽ расчётом (занятий %s, в перерыве %s, %s)",
                    TARGET_ID, ch.calculated_minor // 100,
                    ch.expected_lessons, ch.break_lessons, ch.status,
                )

        # Перерыв ОСТАЁТСЯ на слитой учётке — решение оператора, а не забытая строка.
        brk = (
            await db.execute(
                text(
                    "SELECT count(*) FROM student_break WHERE student_id = :s"
                ),
                {"s": SOURCE_ID},
            )
        ).scalar_one()
        logger.info(
            "Справочно: перерывов на слитой %s — %s (по решению оператора не переносим)",
            SOURCE_ID, brk,
        )
    return ok


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        if not await _preflight(db):
            logger.error("Предпроверка не пройдена — запись не выполняется")
            return 1
        if not apply:
            logger.info("Сухой прогон: ничего не записано. Повторить с --apply.")
            return 0
        try:
            await _apply(db)
        except Exception:
            await db.rollback()
            logger.exception("Запись откачена")
            return 1

    return 0 if await _verify() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
