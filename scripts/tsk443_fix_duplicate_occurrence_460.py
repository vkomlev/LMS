"""tsk-443: точечный фикс реального дубля occurrence на проде (id=460 vs 23).

Контекст: до фикса bookable+join (tsk-443, продолжение №2) `BookLessonSection`
всегда создавал НОВЫЙ ad-hoc occurrence, даже когда введённое время совпадало
с уже существующим занятием. Денис Ильин (student_id=4501) записался на Пн
27.07.26 17:00 МСК — система создала occurrence id=460 (slot_id=NULL,
1 участник) на то же время, что уже существующий occurrence id=23
(slot_id=12, 3 участника: Рита Харькова, Кузнецкий Кирилл Александрович,
Оля Омельченко) — тот же преподаватель (Виктор Комлев).

Действие: переносит участие Дениса (`lesson_occurrence_participant`) и его
`attendance_event` (реальный факт "Виктор отметил Дениса присутствующим" —
НЕ переписывается, только исправляется ссылка на правильный occurrence,
содержание actor/action/timestamp остаётся тем же) с occurrence 460 на 23,
затем удаляет опустевший дубль 460.

Режимы:
- (по умолчанию) — dry-run: печатает план, ничего не пишет.
- `--apply` — выполняет перенос.

Запуск: DBCHECK_OK=1 venv/bin/python scripts/tsk443_fix_duplicate_occurrence_460.py --apply
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsk443_fix_460")

DUPLICATE_OCCURRENCE_ID = 460
CORRECT_OCCURRENCE_ID = 23


async def _print_plan(db) -> None:
    dup = (
        await db.execute(
            text("SELECT id, slot_id, teacher_id, scheduled_at FROM lesson_occurrence WHERE id=:id"),
            {"id": DUPLICATE_OCCURRENCE_ID},
        )
    ).mappings().first()
    correct = (
        await db.execute(
            text("SELECT id, slot_id, teacher_id, scheduled_at FROM lesson_occurrence WHERE id=:id"),
            {"id": CORRECT_OCCURRENCE_ID},
        )
    ).mappings().first()
    print(f"Дубль:   {dict(dup) if dup else 'НЕ НАЙДЕН'}")
    print(f"Верный:  {dict(correct) if correct else 'НЕ НАЙДЕН'}")

    participants = (
        await db.execute(
            text(
                "SELECT id, student_id, status FROM lesson_occurrence_participant "
                "WHERE occurrence_id=:id"
            ),
            {"id": DUPLICATE_OCCURRENCE_ID},
        )
    ).mappings().all()
    print(f"Участники дубля (переедут на {CORRECT_OCCURRENCE_ID}): {[dict(p) for p in participants]}")

    events = (
        await db.execute(
            text(
                "SELECT id, actor_user_id, action, created_at FROM attendance_event "
                "WHERE occurrence_id=:id"
            ),
            {"id": DUPLICATE_OCCURRENCE_ID},
        )
    ).mappings().all()
    print(f"attendance_event дубля (переедут на {CORRECT_OCCURRENCE_ID}): {[dict(e) for e in events]}")


async def _apply(db) -> None:
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET occurrence_id=:correct "
            "WHERE occurrence_id=:dup"
        ),
        {"correct": CORRECT_OCCURRENCE_ID, "dup": DUPLICATE_OCCURRENCE_ID},
    )
    await db.execute(
        text("UPDATE attendance_event SET occurrence_id=:correct WHERE occurrence_id=:dup"),
        {"correct": CORRECT_OCCURRENCE_ID, "dup": DUPLICATE_OCCURRENCE_ID},
    )
    await db.execute(
        text("DELETE FROM lesson_occurrence WHERE id=:dup"), {"dup": DUPLICATE_OCCURRENCE_ID},
    )


async def _verify(db) -> None:
    still_exists = (
        await db.execute(
            text("SELECT COUNT(*) FROM lesson_occurrence WHERE id=:id"),
            {"id": DUPLICATE_OCCURRENCE_ID},
        )
    ).scalar_one()
    assert still_exists == 0, "дубль всё ещё существует"

    denis_row = (
        await db.execute(
            text(
                "SELECT status FROM lesson_occurrence_participant "
                "WHERE occurrence_id=:id AND student_id=4501"
            ),
            {"id": CORRECT_OCCURRENCE_ID},
        )
    ).fetchone()
    assert denis_row is not None, "участие Дениса не найдено на верном occurrence"

    print(f"Верификация OK: дубль id={DUPLICATE_OCCURRENCE_ID} удалён, "
          f"Денис Ильин теперь участник occurrence id={CORRECT_OCCURRENCE_ID} (status={denis_row[0]}).")


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
