"""tsk-455: разовая коррекция расписания Ильи Рвачёва (id=4540 после слияния)
по решению оператора — заменить слот понедельник 10:00 (id=4) на вторник
10:00 (id=3, уже существует в системе с учеником Максим Сундуков).

Действия:
1. Деактивировать участие в слоте 4 (понедельник) через штатный сервис.
2. Удалить его будущие lesson_occurrence_participant в статусе 'scheduled'
   по occurrence слота 4 — штатный remove_slot_participant этого не делает
   (расхождение с докстрингом, отдельно зафиксировано, не в рамках этой
   правки).
3. Добавить участие в слоте 3 (вторник) через штатный сервис — бэкфиллит
   будущие уже сгенерированные occurrence слота 3 автоматически.
4. Задним числом внести явку на СЕГОДНЯШНЕЕ occurrence 5 (слот 3, вторник,
   уже прошло к моменту разбора) — он реально занимался в это окно, но не
   был участником слота 3 на момент занятия.

Запуск на проде (протокол /db-check):
    DBCHECK_OK=1 venv/bin/python scripts/tsk455_fix_ilya_schedule.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services.lesson_calendar_service import (  # noqa: E402
    add_slot_participant,
    remove_slot_participant,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsk455_fix_ilya_schedule")

STUDENT_ID = 4540
OLD_SLOT_ID = 4  # понедельник 10:00
NEW_SLOT_ID = 3  # вторник 10:00
STALE_MONDAY_OCCURRENCE_IDS = [8, 307]  # будущие, status='scheduled'
TODAY_TUESDAY_OCCURRENCE_ID = 5
TEACHER_ID = 2


async def main() -> None:
    async with async_session_factory() as db:
        await remove_slot_participant(db, slot_id=OLD_SLOT_ID, student_id=STUDENT_ID)
        logger.info("remove_slot_participant: слот %s деактивирован", OLD_SLOT_ID)

        res = await db.execute(
            text(
                "DELETE FROM lesson_occurrence_participant "
                "WHERE student_id = :sid AND occurrence_id = ANY(:oids) AND status = 'scheduled'"
            ),
            {"sid": STUDENT_ID, "oids": STALE_MONDAY_OCCURRENCE_IDS},
        )
        logger.info("удалено будущих участий на слоте %s: %s", OLD_SLOT_ID, res.rowcount)
        await db.commit()

    async with async_session_factory() as db:
        await add_slot_participant(
            db, slot_id=NEW_SLOT_ID, student_id=STUDENT_ID, added_by=TEACHER_ID
        )
        logger.info("add_slot_participant: слот %s активирован (+ бэкфилл будущих occurrence)", NEW_SLOT_ID)

    async with async_session_factory() as db:
        exists = (
            await db.execute(
                text(
                    "SELECT id FROM lesson_occurrence_participant "
                    "WHERE occurrence_id = :oid AND student_id = :sid"
                ),
                {"oid": TODAY_TUESDAY_OCCURRENCE_ID, "sid": STUDENT_ID},
            )
        ).first()
        if exists is None:
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                    "VALUES (:oid, :sid, 'confirmed')"
                ),
                {"oid": TODAY_TUESDAY_OCCURRENCE_ID, "sid": STUDENT_ID},
            )
            await db.execute(
                text(
                    "INSERT INTO attendance_event (occurrence_id, actor_user_id, action) "
                    "VALUES (:oid, :actor, 'manual_present')"
                ),
                {"oid": TODAY_TUESDAY_OCCURRENCE_ID, "actor": TEACHER_ID},
            )
            await db.commit()
            logger.info(
                "явка задним числом внесена: occurrence=%s student=%s",
                TODAY_TUESDAY_OCCURRENCE_ID, STUDENT_ID,
            )
        else:
            logger.info("occurrence=%s: участие уже есть, пропуск", TODAY_TUESDAY_OCCURRENCE_ID)

    print("tsk455_fix_ilya_schedule: готово")


if __name__ == "__main__":
    asyncio.run(main())
