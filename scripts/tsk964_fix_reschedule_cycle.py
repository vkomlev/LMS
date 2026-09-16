# -*- coding: utf-8 -*-
"""tsk-964: разорвать цикл rescheduled<->rescheduled у ученика Шахназарян
Артур (id=4614) на среду 16.09.

ЧТО БЫЛО НЕ ТАК
Ученик дважды подряд (разница 9 секунд, 15.09 16:26:58 -> 16:27:07) перенёс
занятие туда-обратно между occurrence 16608 (18:00 МСК, его исходное место) и
occurrence 16544 (17:00 МСК). Из-за бага в `_seat_student_at` (см. фикс в
app/services/lesson_occurrence_service.py, тот же коммит) второй перенос не
реактивировал старую строку участия 111100 (occurrence 16608), а вернул её
как есть — итог: обе строки участия (111100 и 159417) оказались в статусе
`rescheduled`, указывая друг на друга, ни одна не `scheduled`. Ученик остался
без активного места вообще.

ЧТО ДЕЛАЕТСЯ (решение оператора 16.09)
Строка id=111100 (occurrence 16608, 18:00 МСК — последний выбор ученика)
возвращается в status='scheduled', rescheduled_to_occurrence_id=NULL.
Строка id=159417 (occurrence 16544, 17:00 МСК) НЕ ТРОГАЕТСЯ — остаётся
rescheduled, это история переноса. Кнопка «Отменить перенос» не нужна
(оператор явно отказался) — только точечный ремонт этих двух строк.

Перед записью проверяется, что обе строки участия студента 4614 на этих двух
occurrence ровно те же id и в тех же статусах, что были в разведке 16.09 —
если данные успели измениться (ученик или оператор что-то поменяли), скрипт
останавливается, ничего не пишет.

Запуск: dry-run по умолчанию;
        DBCHECK_OK=1 python scripts/tsk964_fix_reschedule_cycle.py --apply
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

STUDENT_ID = 4614
OCC_KEEP = 16608   # 18:00 МСК — последний выбор ученика, реактивируется
OCC_HISTORY = 16544  # 17:00 МСК — остаётся rescheduled (история)
PARTICIPANT_KEEP_ID = 111100
PARTICIPANT_HISTORY_ID = 159417

SELECT_STATE = """
SELECT p.id, p.occurrence_id, p.student_id, p.status,
       p.rescheduled_to_occurrence_id, p.updated_at,
       o.scheduled_at, o.duration_minutes
FROM lesson_occurrence_participant p
JOIN lesson_occurrence o ON o.id = p.occurrence_id
WHERE p.student_id = $1 AND p.occurrence_id = ANY($2::int[])
ORDER BY p.id
"""

UPDATE_KEEP = """
UPDATE lesson_occurrence_participant
SET status = 'scheduled', rescheduled_to_occurrence_id = NULL, updated_at = now()
WHERE id = $1
  AND student_id = $2
  AND occurrence_id = $3
  AND status = 'rescheduled'
  AND rescheduled_to_occurrence_id = $4
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        for arg in cfg["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно.")
    return dsn


def _fmt(row: asyncpg.Record) -> str:
    return (
        f"id={row['id']} occurrence={row['occurrence_id']} "
        f"scheduled_at={row['scheduled_at']} status={row['status']} "
        f"rescheduled_to={row['rescheduled_to_occurrence_id']} "
        f"updated_at={row['updated_at']}"
    )


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            before = {
                r["id"]: r
                for r in await conn.fetch(SELECT_STATE, STUDENT_ID, [OCC_KEEP, OCC_HISTORY])
            }
            print("Текущее состояние:")
            for row in before.values():
                print(f"  {_fmt(row)}")

            if set(before.keys()) != {PARTICIPANT_KEEP_ID, PARTICIPANT_HISTORY_ID}:
                raise AssertionError(
                    f"ожидались ровно строки {{{PARTICIPANT_KEEP_ID}, {PARTICIPANT_HISTORY_ID}}}, "
                    f"нашёл {set(before.keys())} — данные изменились, останавливаюсь"
                )

            keep = before[PARTICIPANT_KEEP_ID]
            history = before[PARTICIPANT_HISTORY_ID]

            if keep["status"] != "rescheduled" or keep["rescheduled_to_occurrence_id"] != OCC_HISTORY:
                raise AssertionError(
                    f"#{PARTICIPANT_KEEP_ID}: ожидал status=rescheduled -> {OCC_HISTORY}, "
                    f"а сейчас {_fmt(keep)} — данные изменились со вчерашней разведки, останавливаюсь"
                )
            if history["status"] != "rescheduled" or history["rescheduled_to_occurrence_id"] != OCC_KEEP:
                raise AssertionError(
                    f"#{PARTICIPANT_HISTORY_ID}: ожидал status=rescheduled -> {OCC_KEEP}, "
                    f"а сейчас {_fmt(history)} — данные изменились со вчерашней разведки, останавливаюсь"
                )
            print(
                "\nПодтверждён цикл: "
                f"{PARTICIPANT_KEEP_ID}(occ={OCC_KEEP}) <-> {PARTICIPANT_HISTORY_ID}(occ={OCC_HISTORY})"
            )

            print(
                f"\nПлан: строка {PARTICIPANT_KEEP_ID} (occurrence {OCC_KEEP}, "
                f"{keep['scheduled_at']}) -> status='scheduled', rescheduled_to_occurrence_id=NULL. "
                f"Строка {PARTICIPANT_HISTORY_ID} (occurrence {OCC_HISTORY}) не меняется."
            )

            res = await conn.execute(
                UPDATE_KEEP, PARTICIPANT_KEEP_ID, STUDENT_ID, OCC_KEEP, OCC_HISTORY,
            )
            updated = int(res.split()[-1])
            print(f"UPDATE затронул строк: {updated} (ожидал 1)")
            if updated != 1:
                raise AssertionError("UPDATE затронул не ровно одну строку — откатываю")

            after = {
                r["id"]: r
                for r in await conn.fetch(SELECT_STATE, STUDENT_ID, [OCC_KEEP, OCC_HISTORY])
            }
            print("\nСостояние после записи:")
            for row in after.values():
                print(f"  {_fmt(row)}")

            keep_after = after[PARTICIPANT_KEEP_ID]
            history_after = after[PARTICIPANT_HISTORY_ID]
            if keep_after["status"] != "scheduled" or keep_after["rescheduled_to_occurrence_id"] is not None:
                raise AssertionError(f"верификация не прошла: {_fmt(keep_after)}")
            if history_after["status"] != "rescheduled" or history_after["rescheduled_to_occurrence_id"] != OCC_KEEP:
                raise AssertionError(
                    f"строка-история изменилась, а не должна была: {_fmt(history_after)}"
                )
            scheduled_rows = [r for r in after.values() if r["status"] == "scheduled"]
            if len(scheduled_rows) != 1 or scheduled_rows[0]["id"] != PARTICIPANT_KEEP_ID:
                raise AssertionError(
                    f"после записи должно быть ровно ОДНО активное место (id={PARTICIPANT_KEEP_ID}), "
                    f"получено: {[r['id'] for r in scheduled_rows]}"
                )
            print(
                "\nВерификация: у ученика ровно одно активное место — "
                f"occurrence {OCC_KEEP} ({keep_after['scheduled_at']}), история на {OCC_HISTORY} не тронута."
            )

            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    argv = sys.argv[1:]
    try:
        asyncio.run(main("--apply" in argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
