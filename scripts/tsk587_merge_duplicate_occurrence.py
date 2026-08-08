"""tsk-587: слить занятие-двойник 5674 с занятием слота 826 (прод, разово).

Занятие 5674 (вт 11.08 10:00 МСК, `slot_id=NULL`) — двойник занятия 826
(тот же час, тот же преподаватель, `slot_id=3`). Породил его
`create_ad_hoc_occurrence`, который до tsk-587 никогда не присоединялся к уже
существующему занятию. Ученик видел два варианта на один час, преподаватель —
два занятия подряд.

Что делает: переносит участие ученика 4508 из 5674 в 826 и удаляет 5674.
Строка ведущего у 5674 уходит каскадом (FK `ON DELETE CASCADE`), событий явки
у него нет, ссылок `rescheduled_to_occurrence_id` на него нет — проверено до
запуска и проверяется ещё раз внутри транзакции.

Порядок важен: сперва перенос участия, потом удаление. Наоборот каскад унёс бы
запись ученика вместе с занятием.

Запуск (прод):
    python scripts/tsk587_merge_duplicate_occurrence.py            # сухой прогон
    DBCHECK_OK=1 python scripts/tsk587_merge_duplicate_occurrence.py --apply

DSN — из `.mcp.json` (прод-хост), не из `.env` (там dev-база).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

DUPLICATE_OCCURRENCE_ID = 5674
TARGET_OCCURRENCE_ID = 826
PARTICIPANT_ID = 26486
STUDENT_ID = 4508

_STATE_SQL = """
SELECT o.id, o.slot_id, o.teacher_id, o.duration_minutes,
       to_char(o.scheduled_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') AS msk,
       (SELECT count(*) FROM lesson_occurrence_participant p WHERE p.occurrence_id = o.id) AS participants
FROM lesson_occurrence o WHERE o.id = ANY($1::int[]) ORDER BY o.id
"""


def prod_dsn() -> str:
    """Строка подключения к боевой базе из `.mcp.json`. Секрет не печатается."""
    config = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json").read_text("utf-8"))
    for arg in config["mcpServers"]["learn_prod_db"]["args"]:
        if arg.startswith("postgresql://"):
            return arg
    raise RuntimeError("в .mcp.json не найдена строка подключения learn_prod_db")


async def show_state(conn: asyncpg.Connection, title: str) -> None:
    print(f"\n=== {title} ===")
    rows = await conn.fetch(_STATE_SQL, [DUPLICATE_OCCURRENCE_ID, TARGET_OCCURRENCE_ID])
    if not rows:
        print("  занятий не найдено")
    for row in rows:
        print(
            f"  занятие {row['id']}: слот={row['slot_id']} преподаватель={row['teacher_id']} "
            f"{row['msk']} МСК {row['duration_minutes']}мин участников={row['participants']}"
        )
    participant = await conn.fetchrow(
        "SELECT occurrence_id, status FROM lesson_occurrence_participant WHERE id = $1",
        PARTICIPANT_ID,
    )
    if participant is None:
        print(f"  участие {PARTICIPANT_ID}: строки нет")
    else:
        print(
            f"  участие {PARTICIPANT_ID} (ученик {STUDENT_ID}): "
            f"занятие={participant['occurrence_id']} статус={participant['status']}"
        )


async def preflight(conn: asyncpg.Connection) -> None:
    """Проверки, без которых правка небезопасна. Любая осечка — стоп."""
    participant = await conn.fetchrow(
        "SELECT occurrence_id, student_id, status FROM lesson_occurrence_participant WHERE id = $1",
        PARTICIPANT_ID,
    )
    if participant is None:
        raise SystemExit(f"СТОП: участия {PARTICIPANT_ID} нет — состояние изменилось")
    if participant["occurrence_id"] != DUPLICATE_OCCURRENCE_ID:
        raise SystemExit(
            f"СТОП: участие {PARTICIPANT_ID} уже в занятии {participant['occurrence_id']}"
        )
    if participant["student_id"] != STUDENT_ID:
        raise SystemExit(f"СТОП: участие {PARTICIPANT_ID} принадлежит другому ученику")
    if participant["status"] != "scheduled":
        raise SystemExit(f"СТОП: статус участия '{participant['status']}', ожидался 'scheduled'")

    clash = await conn.fetchval(
        "SELECT id FROM lesson_occurrence_participant WHERE occurrence_id = $1 AND student_id = $2",
        TARGET_OCCURRENCE_ID, STUDENT_ID,
    )
    if clash is not None:
        raise SystemExit(f"СТОП: ученик уже участник занятия {TARGET_OCCURRENCE_ID} (строка {clash})")

    times = await conn.fetch(
        "SELECT id, scheduled_at, duration_minutes, teacher_id FROM lesson_occurrence "
        "WHERE id = ANY($1::int[])",
        [DUPLICATE_OCCURRENCE_ID, TARGET_OCCURRENCE_ID],
    )
    if len(times) != 2:
        raise SystemExit("СТОП: одно из занятий не найдено")
    first, second = times[0], times[1]
    if first["scheduled_at"] != second["scheduled_at"]:
        raise SystemExit("СТОП: занятия не на одно время — это не двойник")
    if first["duration_minutes"] != second["duration_minutes"]:
        raise SystemExit("СТОП: разная длительность занятий")
    if first["teacher_id"] != second["teacher_id"]:
        raise SystemExit("СТОП: разные преподаватели")

    others = await conn.fetchval(
        "SELECT count(*) FROM lesson_occurrence_participant "
        "WHERE occurrence_id = $1 AND id <> $2",
        DUPLICATE_OCCURRENCE_ID, PARTICIPANT_ID,
    )
    if others:
        raise SystemExit(f"СТОП: в занятии {DUPLICATE_OCCURRENCE_ID} ещё {others} участник(ов)")

    events = await conn.fetchval(
        "SELECT count(*) FROM attendance_event WHERE occurrence_id = $1", DUPLICATE_OCCURRENCE_ID
    )
    if events:
        raise SystemExit(f"СТОП: у занятия {DUPLICATE_OCCURRENCE_ID} есть события явки ({events})")

    incoming = await conn.fetchval(
        "SELECT count(*) FROM lesson_occurrence_participant WHERE rescheduled_to_occurrence_id = $1",
        DUPLICATE_OCCURRENCE_ID,
    )
    if incoming:
        raise SystemExit(f"СТОП: на занятие {DUPLICATE_OCCURRENCE_ID} ссылаются переносы ({incoming})")

    print("\nПредпроверки пройдены: двойник пуст, событий явки и ссылок на него нет.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить правку (иначе сухой прогон)")
    args = parser.parse_args()

    conn = await asyncpg.connect(prod_dsn())
    try:
        await show_state(conn, "ДО")
        await preflight(conn)

        print("\nПлан:")
        print(f"  1. участие {PARTICIPANT_ID} (ученик {STUDENT_ID}): "
              f"занятие {DUPLICATE_OCCURRENCE_ID} -> {TARGET_OCCURRENCE_ID}")
        print(f"  2. удалить занятие {DUPLICATE_OCCURRENCE_ID} "
              f"(строка ведущего уйдёт каскадом)")

        if not args.apply:
            print("\nСухой прогон — ничего не менялось. Для правки: --apply")
            return

        async with conn.transaction():
            moved = await conn.execute(
                "UPDATE lesson_occurrence_participant "
                "SET occurrence_id = $1, updated_at = now() "
                "WHERE id = $2 AND occurrence_id = $3",
                TARGET_OCCURRENCE_ID, PARTICIPANT_ID, DUPLICATE_OCCURRENCE_ID,
            )
            if moved != "UPDATE 1":
                raise RuntimeError(f"перенос участия затронул не одну строку: {moved}")

            left = await conn.fetchval(
                "SELECT count(*) FROM lesson_occurrence_participant WHERE occurrence_id = $1",
                DUPLICATE_OCCURRENCE_ID,
            )
            if left:
                raise RuntimeError(f"в двойнике осталось {left} участник(ов) — удаление отменено")

            removed = await conn.execute(
                "DELETE FROM lesson_occurrence WHERE id = $1", DUPLICATE_OCCURRENCE_ID
            )
            if removed != "DELETE 1":
                raise RuntimeError(f"удаление занятия затронуло не одну строку: {removed}")

        await show_state(conn, "ПОСЛЕ")
        print("\nГотово: участие перенесено, двойник удалён.")
    finally:
        await conn.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
