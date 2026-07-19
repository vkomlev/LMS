"""tsk-318 — перетегировать вводные/контрольные задания ЕГЭ в THEORY.

Дефект импорта: WP-авторские вводные задания и контрольные вопросы получили
external_uid вида `lms:cNNN:vvod:NN`, но при импорте не смаппились в сложность
THEORY (id=1) — контрольные вопросы (SC/MC) осели корректно, а вводные задания
(SA_COM) упали в EASY(2)/NORMAL(3)/HARD(4) по авто-сложности.

Сигнал (валидирован на якорях): `external_uid LIKE 'lms:c%:vvod:%'`.
  - Это единственный маркер WP-авторских заданий (все имеют wp: course_uid).
  - 161 задание в 11 подкурсах навигатора ЕГЭ (112).
  - Якоря: курс 139 THEORY 5 -> 27 (ожид. ~30); курс 158 THEORY 8 -> 30 (ожид. «много»).

Правило записи: difficulty_id -> 1 для vvod-заданий с difficulty_id <> 1 (135 строк).

order_position НЕ трогаем. Триггер trg_set_task_order_position при UPDATE, где
order_position не меняется, делает no-op (NEW.order_position = OLD -> RETURN NEW),
поэтому реордера/каскада не будет. Реордер по сложности — display-слой SPW
(движок next-item ставит THEORY вперёд); требуется инвалидация SPW queryKey
`syllabus-states` для затронутых курсов. Обратимо: вернуть difficulty_id из бэкапа.

Прод-DSN читается из .mcp.json (learn_prod_db) — секрет не хранится в этом файле.

Запуск:
    python scripts/retag_vvod_theory_tsk318.py            # dry-run (ROLLBACK)
    DBCHECK_OK=1 python scripts/retag_vvod_theory_tsk318.py --apply   # COMMIT
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
VVOD_PREDICATE = "is_active = true AND external_uid LIKE 'lms:c%:vvod:%'"


def load_prod_dsn() -> str:
    """Достать прод-DSN роли lms_prod из .mcp.json (секрет не хардкодим)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    # asyncpg не принимает ?options=...; пароль percent-decoded вручную.
    return (
        f"postgresql://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def snapshot(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Проекция до/после по vvod-курсам."""
    return await conn.fetch(
        f"""
        WITH v AS (
            SELECT course_id, difficulty_id,
                   (external_uid LIKE 'lms:c%:vvod:%') AS is_vvod
            FROM tasks WHERE is_active = true
        )
        SELECT course_id,
               count(*) FILTER (WHERE is_vvod) AS vvod_total,
               count(*) FILTER (WHERE is_vvod AND difficulty_id <> 1) AS vvod_non_theory,
               count(*) FILTER (WHERE difficulty_id = 1) AS theory_total
        FROM v
        WHERE course_id IN (SELECT DISTINCT course_id FROM v WHERE is_vvod)
        GROUP BY course_id ORDER BY course_id
        """
    )


def print_snapshot(title: str, rows: list[asyncpg.Record]) -> None:
    print(f"\n{title}")
    print(f"  {'course':>7} {'vvod':>5} {'vvod!=THEORY':>12} {'THEORY':>7}")
    for r in rows:
        mark = "  <-- ЯКОРЬ" if r["course_id"] in (139, 158) else ""
        print(
            f"  {r['course_id']:>7} {r['vvod_total']:>5} "
            f"{r['vvod_non_theory']:>12} {r['theory_total']:>7}{mark}"
        )


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-318 retag vvod -> THEORY — {mode} ===")

    conn = await asyncpg.connect(load_prod_dsn())
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            before = await snapshot(conn)
            print_snapshot("BEFORE:", before)

            # снимок order_position затронутых строк — доказать неизменность
            op_before = {
                r["id"]: r["order_position"]
                for r in await conn.fetch(
                    f"SELECT id, order_position FROM tasks "
                    f"WHERE {VVOD_PREDICATE} AND difficulty_id <> 1"
                )
            }

            status = await conn.execute(
                f"UPDATE tasks SET difficulty_id = 1 "
                f"WHERE {VVOD_PREDICATE} AND difficulty_id <> 1"
            )
            updated = int(status.split()[-1])
            print(f"\nUPDATE rowcount = {updated} (ожидалось 135)")

            after = await snapshot(conn)
            print_snapshot("AFTER:", after)

            # инварианты
            remaining = await conn.fetchval(
                f"SELECT count(*) FROM tasks WHERE {VVOD_PREDICATE} AND difficulty_id <> 1"
            )
            op_after = {
                r["id"]: r["order_position"]
                for r in await conn.fetch(
                    f"SELECT id, order_position FROM tasks WHERE id = ANY($1::int[])",
                    list(op_before.keys()),
                )
            }
            moved = [i for i, op in op_before.items() if op_after.get(i) != op]
            nonvvod_theory_touched = await conn.fetchval(
                "SELECT count(*) FROM tasks "
                "WHERE difficulty_id = 1 AND external_uid NOT LIKE 'lms:c%:vvod:%' "
                "AND is_active = true"
            )

            print("\n--- Инварианты ---")
            print(f"  vvod с difficulty<>1 после UPDATE: {remaining} (ожид. 0)")
            print(f"  строк с изменённым order_position: {len(moved)} (ожид. 0)")
            print(f"  не-vvod THEORY в БД (не должны меняться): {nonvvod_theory_touched}")

            a139 = next((r for r in after if r["course_id"] == 139), None)
            a158 = next((r for r in after if r["course_id"] == 158), None)
            print("\n--- Якоря ---")
            print(f"  курс 139 THEORY = {a139['theory_total'] if a139 else '?'} (ожид. ~30 -> 27)")
            print(f"  курс 158 THEORY = {a158['theory_total'] if a158 else '?'} (ожид. «много» -> 30)")

            ok = remaining == 0 and len(moved) == 0
            if apply and ok:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                if apply and not ok:
                    print("\nROLLBACK — инварианты нарушены, изменения откатаны.")
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
