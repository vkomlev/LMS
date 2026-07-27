"""tsk-440: добавить двух преподавателей в расписание (Серебрякова Екатерина
id=3 + Коротких Светлана id=4495 — Пн 11:00, Ср 10:00, Ср 11:00 каждой) и
третьего на всю субботу (Ладесов Кирилл id=4496 — Сб 10:00, Сб 11:00). Все
слоты "совместно с оператором" (teacher_id=2) — его собственные слоты на эти
же дни/часы НЕ трогаются, это отдельные параллельные группы под другим
преподавателем в то же время.

Все трое уже существуют в БД с ролью teacher (проверено read-only перед
запуском) — новых аккаунтов заводить не нужно, только слоты.

Режимы:
- `--dry-run` (по умолчанию) — только печатает план, ничего не пишет.
- `--apply` — создаёт слоты через тот же сервисный слой, что использует
  admin API (`lesson_calendar_service.create_lesson_slot`).

Запуск на проде: DBCHECK_OK=1 venv/bin/python scripts/tsk440_add_teachers_schedule.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services import lesson_calendar_service  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsk440_add_teachers")

TEACHERS_MON_WED = {
    3: "Серебрякова Екатерина",
    4495: "Коротких Светлана",
}
TEACHER_SATURDAY = {4496: "Ладесов Кирилл"}

# (weekday, start_time, duration_minutes) — timezone Europe/Moscow, как у всех
# остальных слотов. Пн=0, Ср=2, Сб=5.
SLOTS_MON_WED = [
    (0, time(11, 0), 60),  # Пн 11:00
    (2, time(10, 0), 60),  # Ср 10:00
    (2, time(11, 0), 60),  # Ср 11:00
]
SLOTS_SATURDAY = [
    (5, time(10, 0), 60),  # Сб 10:00 (то же время, что у оператора)
    (5, time(11, 0), 60),  # Сб 11:00 (то же время, что у оператора)
]


def _print_plan() -> None:
    print("Слоты (все timezone=Europe/Moscow, teacher-only overlap-check — параллельны слотам оператора):")
    for teacher_id, label in TEACHERS_MON_WED.items():
        print(f"  {label} (id={teacher_id}):")
        for weekday, start_time, duration in SLOTS_MON_WED:
            print(f"    weekday={weekday} start={start_time} duration={duration}")
    for teacher_id, label in TEACHER_SATURDAY.items():
        print(f"  {label} (id={teacher_id}):")
        for weekday, start_time, duration in SLOTS_SATURDAY:
            print(f"    weekday={weekday} start={start_time} duration={duration}")


async def _apply() -> None:
    async with async_session_factory() as db:
        for teacher_id, label in TEACHERS_MON_WED.items():
            for weekday, start_time, duration in SLOTS_MON_WED:
                row = await lesson_calendar_service.create_lesson_slot(
                    db, teacher_id=teacher_id, weekday=weekday, start_time=start_time,
                    duration_minutes=duration, timezone="Europe/Moscow",
                    created_by=2, student_ids=[],
                )
                logger.info("%s: слот id=%s weekday=%s start=%s", label, row.id, weekday, start_time)

        for teacher_id, label in TEACHER_SATURDAY.items():
            for weekday, start_time, duration in SLOTS_SATURDAY:
                row = await lesson_calendar_service.create_lesson_slot(
                    db, teacher_id=teacher_id, weekday=weekday, start_time=start_time,
                    duration_minutes=duration, timezone="Europe/Moscow",
                    created_by=2, student_ids=[],
                )
                logger.info("%s: слот id=%s weekday=%s start=%s", label, row.id, weekday, start_time)

    print("\nГотово.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Выполнить запись (по умолчанию — dry-run)")
    args = parser.parse_args()

    _print_plan()
    if not args.apply:
        print("\n[dry-run] Ничего не записано. Запустите с --apply для реальной записи.")
        return

    asyncio.run(_apply())


if __name__ == "__main__":
    main()
