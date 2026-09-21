"""tsk-1021: обновить устаревший ручной override цены Ундасыновой (прод).

Ученица Василина Ундасынова (student_id=4549) 01.08.2026 была записана на
2 занятия в неделю (пн 17:00, ср 18:00) и получила ручную цену
`student_price_override` id=5 = 550000 (5500 ₽) — ступень «2 раза в неделю»
тарифа «Базовый» (group_id=1). Верно на тот момент.

31.08.2026 расписание сменили на 1 занятие в неделю (сб 12:00), а override
не тронули. С этого момента система продолжает считать по старой цене
2x/нед, хотя факт — 1x/нед. Ступень «1 раз в неделю» того же тарифа =
275000 (2750 ₽).

Точной даты деактивации пн/ср слотов в БД нет (у `lesson_slot_student` нет
`updated_at`, в `audit_event` нет событий по расписанию) — разведка задачи
это уже установила. Поэтому август (`student_monthly_charge` id=38, закрыт,
оплачен 14.08) этим скриптом НЕ трогается и трогаться не должен: правило
`recalculate_open_months_for_student` (tsk-756) и так пересчитывает только
текущий и будущие открытые месяцы, прошлое остаётся как есть.

Протокол (`/db-check`, режим записи):
    python scripts/tsk1021_fix_undasynova_override.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk1021_fix_undasynova_override.py --apply

Запускать на сервере под `app` (R-009 operator-runbook):
    ssh lms-spw-vds 'sudo -u app bash -lc "cd /opt/lms && venv/bin/python \
        scripts/tsk1021_fix_undasynova_override.py --apply"'
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
logger = logging.getLogger("tsk1021.fix")

STUDENT_ID = 4549
GROUP_ID = 1
OLD_PRICE_MINOR = 550000
NEW_PRICE_MINOR = 275000
SEPTEMBER_PERIOD = date(2026, 9, 1)
AUGUST_PERIOD = date(2026, 8, 1)

#: Кто правит — тот же операторский аккаунт, что создал исходный override 01.08.
OPERATOR_ID = 2

NOTE = (
    "tsk-1021: цена выставлена 01.08 под расписание 2 раза/нед (пн+ср), "
    "которое 31.08 заменили на 1 раз/нед (сб) без правки override. "
    "Возвращена к ступени тарифа «1 раз в неделю»."
)


async def show_state(db, title: str) -> None:
    logger.info("\n===== %s =====", title)
    override = (
        await db.execute(
            text(
                "SELECT price_minor, note, ends_on, updated_at "
                "  FROM student_price_override WHERE id = 5"
            )
        )
    ).first()
    logger.info(
        "override id=5: price_minor=%s note=%r ends_on=%s updated_at=%s",
        override.price_minor,
        override.note,
        override.ends_on,
        override.updated_at,
    )
    for label, period, charge_id in (
        ("сентябрь", SEPTEMBER_PERIOD, 153),
        ("август", AUGUST_PERIOD, 38),
    ):
        charge = (
            await db.execute(
                text(
                    "SELECT calculated_minor, manual_minor, expected_lessons, status "
                    "  FROM student_monthly_charge WHERE id = :id"
                ),
                {"id": charge_id},
            )
        ).first()
        logger.info(
            "%s (id=%d): calculated_minor=%s manual_minor=%s expected_lessons=%s status=%s",
            label,
            charge_id,
            charge.calculated_minor,
            charge.manual_minor,
            charge.expected_lessons,
            charge.status,
        )


async def verify(db) -> bool:
    logger.info("\n===== Проверка после записи =====")
    ok = True

    override = (
        await db.execute(
            text("SELECT price_minor FROM student_price_override WHERE id = 5")
        )
    ).scalar_one()
    if override != NEW_PRICE_MINOR:
        logger.error("override id=5: price_minor=%s, ждали %s", override, NEW_PRICE_MINOR)
        ok = False

    september = (
        await db.execute(
            text(
                "SELECT calculated_minor, expected_lessons, status "
                "  FROM student_monthly_charge WHERE id = 153"
            )
        )
    ).one()
    if september.calculated_minor != NEW_PRICE_MINOR:
        logger.error(
            "сентябрь id=153: calculated_minor=%s, ждали %s",
            september.calculated_minor,
            NEW_PRICE_MINOR,
        )
        ok = False
    if september.expected_lessons != 4:
        logger.error(
            "сентябрь id=153: expected_lessons=%s, ждали 4 (не должно было измениться)",
            september.expected_lessons,
        )
        ok = False
    if september.status != "open":
        logger.error("сентябрь id=153: status=%s, ждали open", september.status)
        ok = False

    august = (
        await db.execute(
            text(
                "SELECT calculated_minor, status "
                "  FROM student_monthly_charge WHERE id = 38"
            )
        )
    ).one()
    if august.calculated_minor != OLD_PRICE_MINOR or august.status != "closed":
        logger.error(
            "август id=38 тронут: calculated_minor=%s status=%s (ждали %s/closed)",
            august.calculated_minor,
            august.status,
            OLD_PRICE_MINOR,
        )
        ok = False

    logger.info("Итог: %s", "ОК" if ok else "ПРОБЛЕМА — см. выше")
    return ok


async def main(apply: bool) -> int:
    async with async_session_factory() as db:
        await show_state(db, "До правки")

        logger.info("\n===== План =====")
        logger.info(
            "student_id=%s group_id=%s: price_minor %s -> %s, "
            "затем recalculate_open_months_for_student (пересчитает только "
            "сентябрь id=153 и будущее — август id=38 закрыт, не входит)",
            STUDENT_ID,
            GROUP_ID,
            OLD_PRICE_MINOR,
            NEW_PRICE_MINOR,
        )

        if not apply:
            logger.info("\nСухой прогон. Записи не было. Повторить с --apply.")
            return 0

        await charge_service.set_price_override(
            db,
            student_id=STUDENT_ID,
            group_id=GROUP_ID,
            price_minor=NEW_PRICE_MINOR,
            note=NOTE,
            created_by=OPERATOR_ID,
            ends_on=None,
        )
        logger.info("override id=5: price_minor обновлён на %s", NEW_PRICE_MINOR)

        await charge_service.recalculate_open_months_for_student(db, student_id=STUDENT_ID)
        logger.info("recalculate_open_months_for_student выполнен")

        await show_state(db, "После правки")
        return 0 if await verify(db) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
