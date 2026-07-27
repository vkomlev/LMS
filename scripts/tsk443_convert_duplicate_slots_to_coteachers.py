"""tsk-443: превратить отдельные слоты tsk-440 в со-преподавание.

Контекст: tsk-440 добавил Серебряковой/Коротких/Ладесову ОТДЕЛЬНЫЕ слоты
(id 14-21) на те же часы, что и у оператора (id=2), в расчёте на модель
"каждому преподавателю — своё отдельное occurrence". Оператор живьём открыл
календарь Серебряковой — 0 участников (в отдельные слоты никто не был
добавлен). Прямой запрос: ученики должны быть видны СРАЗУ всем
преподавателям одного занятия, явка общая. Архитектура (tsk-443,
подтверждена AskUserQuestion): ОДНО occurrence на несколько преподавателей
(M2M lesson_slot_teacher/lesson_occurrence_teacher), а не отдельные слоты.

Это делает:
1. Добавляет Серебрякову (id=3) и Коротких (id=4495) со-преподавателями
   слотов оператора id=10 (Пн 11:00), id=6 (Ср 10:00), id=8 (Ср 11:00).
2. Добавляет Ладесова (id=4496) со-преподавателем слотов оператора id=1
   (Сб 11:00), id=2 (Сб 10:00).
   `add_slot_teacher` бэкфиллит уже сгенерированные будущие occurrence этих
   слотов — со-преподаватели увидят их сразу, не дожидаясь тика генератора.
3. Деактивирует 8 отдельных слотов tsk-440 (id 14-21) — они больше не
   нужны, замена на со-преподавание. Слоты не удаляются (soft-delete,
   `is_active=false`), но их будущие occurrence (0 участников у каждого,
   проверено read-only перед запуском) — удаляются полностью: это чистый
   мусор без единой строки истории, оставлять их в календарях смысла нет.

Режимы:
- (по умолчанию) — dry-run: печатает план, ничего не пишет.
- `--apply` — выполняет перенос.

Запуск: DBCHECK_OK=1 venv/bin/python scripts/tsk443_convert_duplicate_slots_to_coteachers.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env", encoding="utf-8-sig")

from app.db.session import async_session_factory  # noqa: E402
from app.services import lesson_calendar_service  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsk443_convert")

OPERATOR_ID = 2  # Комлев Виктор

# slot_id (оператора) -> [со-преподаватели]
CO_TEACH_PLAN = {
    10: [3, 4495],  # Пн 11:00 — Серебрякова, Коротких
    6: [3, 4495],   # Ср 10:00
    8: [3, 4495],   # Ср 11:00
    1: [4496],      # Сб 11:00 — Ладесов
    2: [4496],      # Сб 10:00
}

DUPLICATE_SLOT_IDS = [14, 15, 16, 17, 18, 19, 20, 21]


async def _print_plan(db) -> None:
    print("Со-преподавание (add_slot_teacher):")
    for slot_id, teacher_ids in CO_TEACH_PLAN.items():
        print(f"  slot_id={slot_id}: + {teacher_ids}")

    print(f"\nДеактивация дублирующих слотов tsk-440: {DUPLICATE_SLOT_IDS}")
    for slot_id in DUPLICATE_SLOT_IDS:
        occ_count, participant_count = (
            await db.execute(
                text(
                    "SELECT COUNT(DISTINCT lo.id), COUNT(lop.id) "
                    "FROM lesson_occurrence lo "
                    "LEFT JOIN lesson_occurrence_participant lop ON lop.occurrence_id = lo.id "
                    "WHERE lo.slot_id = :sid"
                ),
                {"sid": slot_id},
            )
        ).one()
        print(f"  slot_id={slot_id}: occurrence={occ_count}, участников во всех={participant_count}")


async def _apply(db) -> None:
    for slot_id, teacher_ids in CO_TEACH_PLAN.items():
        for teacher_id in teacher_ids:
            row = await lesson_calendar_service.add_slot_teacher(
                db, slot_id, teacher_id, added_by=OPERATOR_ID,
            )
            logger.info("add_slot_teacher: slot=%s teacher=%s -> row_id=%s", slot_id, teacher_id, row.id)

    for slot_id in DUPLICATE_SLOT_IDS:
        # Участников — 0 (проверено в dry-run перед запуском с --apply),
        # occurrence этого слота — чистый мусор, удаляем полностью.
        await db.execute(
            text("DELETE FROM lesson_occurrence WHERE slot_id = :sid"), {"sid": slot_id},
        )
        await lesson_calendar_service.deactivate_lesson_slot(db, slot_id)
        logger.info("slot_id=%s деактивирован, occurrence удалены", slot_id)


async def _verify(db) -> None:
    for slot_id, teacher_ids in CO_TEACH_PLAN.items():
        rows = (
            await db.execute(
                text(
                    "SELECT teacher_id FROM lesson_slot_teacher "
                    "WHERE slot_id = :sid AND is_active = true"
                ),
                {"sid": slot_id},
            )
        ).scalars().all()
        for teacher_id in teacher_ids:
            assert teacher_id in rows, f"slot={slot_id} teacher={teacher_id} не найден в lesson_slot_teacher"

    for slot_id in DUPLICATE_SLOT_IDS:
        is_active = (
            await db.execute(
                text("SELECT is_active FROM lesson_slot WHERE id = :sid"), {"sid": slot_id},
            )
        ).scalar_one()
        assert is_active is False, f"slot_id={slot_id} не деактивирован"
        occ_count = (
            await db.execute(
                text("SELECT COUNT(*) FROM lesson_occurrence WHERE slot_id = :sid"), {"sid": slot_id},
            )
        ).scalar_one()
        assert occ_count == 0, f"slot_id={slot_id} всё ещё имеет occurrence"

    print("Верификация OK.")


async def _run(apply: bool) -> None:
    async with async_session_factory() as db:
        await _print_plan(db)

        if not apply:
            print("\n[dry-run] Ничего не изменено. Запустите с --apply.")
            return

        await _apply(db)
        await db.commit()

    async with async_session_factory() as verify_db:
        await _verify(verify_db)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Выполнить перенос (по умолчанию — dry-run)")
    args = parser.parse_args()
    asyncio.run(_run(args.apply))


if __name__ == "__main__":
    main()
