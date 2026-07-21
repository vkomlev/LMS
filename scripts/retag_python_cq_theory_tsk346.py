"""tsk-346 — перетегировать блоки «Контрольные вопросы» Python-курсов в THEORY.

Тот же класс дефекта, что [[tsk-318]] (см. rollback_vvod_theory_tsk318.sql),
другой охват: не ЕГЭ-номерные курсы (`lms:c%:vvod:%`), а тематические
Python-курсы Комлева (`wp:...`). Живая находка (оператор, 2026-07-21): курс 111
«Условные конструкции», задания 193/204 — блок «Контрольные вопросы» с WP
(после каждой подтемы урока, подтверждено WebFetch живой страницы
victor-komlev.ru/uslovnye-konstruktsii-v-python/: 5 групп вопросов "Вопросы"
после подтем, 6+3+5+5+2=21 вопрос — совпадает с cq:0..cq:4 в БД), но при
импорте не все получили difficulty_id=THEORY (SC/MC/SA_COM встречаются во
всех группах — тип задания НЕНАДЁЖНЫЙ сигнал, предупреждение оператора).

Сигнал (валидирован на якорях 193/204 курса 111):
    external_uid LIKE '%:cq:%' AND course_id BETWEEN 103 AND 111
  - Паттерн `wp:task:komlev:<slug>:cq:<group>:<index>`.
  - Курс 109 (списки) в диапазоне присутствует, но cq-заданий не имеет (0) —
    не влияет на выборку.
  - Курс 561 (legacy-архив, тот же паттерн, 133 cq/99 non-theory) — ИСКЛЮЧЁН
    по решению оператора (не входит в семейство активных тем, 0 попыток
    учеников, self_guided/не обязательный/не демо).

Правило записи: difficulty_id -> 1 (THEORY) для cq-заданий 103..111 с
difficulty_id <> 1 (54 строки: 104:6, 105:7, 106:1, 107:10, 108:6, 110:12,
111:12; курс 103 уже все THEORY).

order_position НЕ трогаем в этом скрипте — реордер отдельным шагом
(scripts/reorder_courses_by_difficulty_tsk345.py --course-min 103 --course-max 111),
т.к. запись идёт прямым SQL в обход TasksService.bulk_upsert (durable-хук
tsk-345 не триггерится). Обратимо: вернуть difficulty_id из бэкапа снапшота.

Прод-DSN читается из .mcp.json (learn_prod_db) — секрет не хранится в этом файле.

Запуск:
    python scripts/retag_python_cq_theory_tsk346.py                  # dry-run (ROLLBACK)
    DBCHECK_OK=1 python scripts/retag_python_cq_theory_tsk346.py --apply   # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CQ_PREDICATE = (
    "is_active = true AND external_uid LIKE '%:cq:%' "
    "AND course_id BETWEEN 103 AND 111"
)
ANCHOR_TASK_IDS = (193, 204)  # курс 111, живая находка


def load_prod_dsn() -> str:
    """Достать прод-DSN роли lms_prod из .mcp.json (секрет не хардкодим)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def snapshot(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Проекция до/после по cq-курсам 103..111."""
    return await conn.fetch(
        f"""
        WITH v AS (
            SELECT course_id, difficulty_id,
                   (external_uid LIKE '%:cq:%') AS is_cq
            FROM tasks WHERE is_active = true AND course_id BETWEEN 103 AND 111
        )
        SELECT course_id,
               count(*) FILTER (WHERE is_cq) AS cq_total,
               count(*) FILTER (WHERE is_cq AND difficulty_id <> 1) AS cq_non_theory,
               count(*) FILTER (WHERE difficulty_id = 1) AS theory_total
        FROM v
        GROUP BY course_id ORDER BY course_id
        """
    )


def print_snapshot(title: str, rows: list[asyncpg.Record]) -> None:
    print(f"\n{title}")
    print(f"  {'course':>7} {'cq':>4} {'cq!=THEORY':>11} {'THEORY':>7}")
    for r in rows:
        mark = "  <-- ЯКОРЬ-КУРС" if r["course_id"] == 111 else ""
        print(
            f"  {r['course_id']:>7} {r['cq_total']:>4} "
            f"{r['cq_non_theory']:>11} {r['theory_total']:>7}{mark}"
        )


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-346 retag python cq -> THEORY — {mode} ===")

    conn = await asyncpg.connect(load_prod_dsn())
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            before = await snapshot(conn)
            print_snapshot("BEFORE:", before)

            anchors_before = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id FROM tasks "
                "WHERE id = ANY($1::int[])",
                list(ANCHOR_TASK_IDS),
            )
            print("\nЯкоря ДО:")
            for r in anchors_before:
                print(f"  id={r['id']} {r['external_uid']} difficulty_id={r['difficulty_id']}")

            op_before = {
                r["id"]: r["order_position"]
                for r in await conn.fetch(
                    f"SELECT id, order_position FROM tasks "
                    f"WHERE {CQ_PREDICATE} AND difficulty_id <> 1"
                )
            }

            status = await conn.execute(
                f"UPDATE tasks SET difficulty_id = 1 "
                f"WHERE {CQ_PREDICATE} AND difficulty_id <> 1"
            )
            updated = int(status.split()[-1])
            print(f"\nUPDATE rowcount = {updated} (ожидалось 54)")

            after = await snapshot(conn)
            print_snapshot("AFTER:", after)

            remaining = await conn.fetchval(
                f"SELECT count(*) FROM tasks WHERE {CQ_PREDICATE} AND difficulty_id <> 1"
            )
            op_after = {
                r["id"]: r["order_position"]
                for r in await conn.fetch(
                    "SELECT id, order_position FROM tasks WHERE id = ANY($1::int[])",
                    list(op_before.keys()),
                )
            }
            moved = [i for i, op in op_before.items() if op_after.get(i) != op]

            other_courses_theory_touched = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE difficulty_id = 1 "
                "AND (course_id < 103 OR course_id > 111) AND is_active = true"
            )
            course_561_touched = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE course_id = 561 AND difficulty_id = 1"
            )

            anchors_after = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id FROM tasks "
                "WHERE id = ANY($1::int[])",
                list(ANCHOR_TASK_IDS),
            )
            print("\nЯкоря ПОСЛЕ:")
            anchors_ok = True
            for r in anchors_after:
                ok = r["difficulty_id"] == 1
                anchors_ok = anchors_ok and ok
                print(
                    f"  id={r['id']} {r['external_uid']} difficulty_id={r['difficulty_id']} "
                    f"{'OK' if ok else 'FAIL'}"
                )

            print("\n--- Инварианты ---")
            print(f"  cq(103..111) с difficulty<>1 после UPDATE: {remaining} (ожид. 0)")
            print(f"  строк с изменённым order_position: {len(moved)} (ожид. 0, реордер отдельным скриптом)")
            print(f"  курс 561 (legacy, вне охвата) THEORY-строк тронуто: {course_561_touched} (ожид. 0 — этот скрипт его не трогает)")

            ok = remaining == 0 and len(moved) == 0 and anchors_ok
            if apply and ok:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                if apply and not ok:
                    print("\nROLLBACK — инварианты/якоря нарушены, изменения откатаны.")
                    return 1
                print("\nROLLBACK — dry-run, изменения откатаны.")
        except BaseException:
            await tx.rollback()
            raise
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
