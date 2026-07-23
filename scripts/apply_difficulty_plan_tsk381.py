"""tsk-381 — применение сложности по сведённому плану канонов.

Скрипт план-ориентированный: работает с любым планом того же формата.
Планы собирают `scripts/tsk381_decide_kompege.py` (партия kompege) и
`scripts/tsk381_decide_tg_canon.py` (sdamgia / Поляков / Яндекс); готовый план
фиксируется в `reviews/tsk381/`.

Приоритет канонов: ТГ-разбор → ручной вердикт оператора → оценка внешнего сайта.
Шкала kompege подтверждена селектором на самом сайте: 0 = Базовый, 1 = Средний,
2 = Сложный.

Применяются только строки, где значение меняется И правка НЕ требует переноса
между базовым курсом и блоком «Сложные» (инвариант `difficulty_id = 4` ⟺ курс
из блока 1379-1403). Строки с переносом и конфликты канонов остаются оператору.

Каждая строка плана проверяется по состоянию «до»: если поле уже другое —
кто-то изменил параллельно, СТОП без записи.

Реордер: прямая запись идёт мимо `TasksService.bulk_upsert`, durable-хук tsk-345
не сработает — реордер вызывается здесь той же ROW_NUMBER-логикой. Триггер
`trg_set_task_order_position` глушится session-variable (is_local=true), НЕ через
`ALTER TABLE ... DISABLE TRIGGER` (ACCESS EXCLUSIVE лок на всю `tasks`,
урок tsk-345/346).

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/apply_kompege_difficulty_tsk381.py
    PROD_DB_DSN=... python scripts/apply_kompege_difficulty_tsk381.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

_LMS_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN = _LMS_ROOT / "reviews" / "tsk381" / "kompege-plan-2026-07-23.json"

BLOCK_MIN, BLOCK_MAX = 1379, 1403
BASE_MIN, BASE_MAX = 138, 165
HARD = 4

ORDER_BY_EXPR = """
    PARTITION BY course_id
    ORDER BY
        difficulty_id ASC,
        CASE task_content->>'type'
            WHEN 'SC' THEN 1
            WHEN 'MC' THEN 1
            WHEN 'TA' THEN 2
            WHEN 'SA' THEN 2
            WHEN 'SA_COM' THEN 3
            ELSE 99
        END ASC,
        order_position ASC NULLS LAST,
        id ASC
"""


def load_fixes(plan_path: Path) -> list[dict[str, Any]]:
    """Строки плана к применению: значение меняется, переноса курса не требуют."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))["plan"]
    return [
        {
            "id": row["id"], "course_id": row["course_id"],
            "before": row["difficulty_id"], "after": row["decided_difficulty_id"],
            "external_uid": row["external_uid"],
            "canon": row.get("canon") or row.get("evidence"),
        }
        for row in plan
        if row.get("changes") and not row.get("needs_course_move")
    ]


async def main(apply: bool, plan_path: Path) -> int:
    """Применяет план в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    fixes = load_fixes(plan_path)
    if not fixes:
        print("В плане нет строк к применению.")
        return 1
    courses = sorted({f["course_id"] for f in fixes})
    ids = [f["id"] for f in fixes]
    expected_after = {f["id"]: f["after"] for f in fixes}

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-381: {len(fixes)} заданий, курсы {courses} — {mode} ===")
    print(f"план: {plan_path}\n")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, order_position "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"BEFORE: найдено {len(rows)} из {len(ids)} ожидаемых заданий.")

        problems: list[str] = []
        for fix in fixes:
            row = by_id.get(fix["id"])
            if row is None:
                problems.append(f"id={fix['id']}: не найдено в БД")
                continue
            if row["external_uid"] != fix["external_uid"] or row["course_id"] != fix["course_id"]:
                problems.append(
                    f"id={fix['id']}: курс/uid не совпадают с планом "
                    f"(план {fix['course_id']}/{fix['external_uid']}, "
                    f"факт {row['course_id']}/{row['external_uid']})"
                )
            if row["difficulty_id"] != fix["before"]:
                problems.append(
                    f"id={fix['id']}: difficulty_id уже не {fix['before']} "
                    f"(факт {row['difficulty_id']}) — кто-то изменил параллельно, СТОП"
                )
            if fix["after"] == HARD or not (BASE_MIN <= fix["course_id"] <= BASE_MAX):
                problems.append(
                    f"id={fix['id']}: строка задевает блок «Сложные» — в этом скрипте не место"
                )

        if problems:
            print("\nОШИБКА, обновление не выполняется:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("состояние «до» совпадает с планом по всем строкам — OK")

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )

            updated = 0
            for fix in fixes:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1 WHERE id = $2 AND difficulty_id = $3",
                    fix["after"], fix["id"], fix["before"],
                )
                updated += int(result.split()[-1])
            print(f"UPDATE difficulty: строк изменено {updated} из {len(fixes)}")
            if updated != len(fixes):
                print("ОШИБКА: изменено не столько строк, сколько в плане — ROLLBACK")
                await tx.rollback()
                return 1

            reorder = await conn.execute(f"""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                    FROM tasks
                    WHERE course_id = ANY($1::int[])
                )
                UPDATE tasks t
                SET order_position = n.new_op
                FROM new_order n
                WHERE t.id = n.id
                  AND (t.order_position IS DISTINCT FROM n.new_op)
            """, courses)
            print(f"REORDER (курсы {courses}): {reorder}")

            # --- Построчная верификация внутри транзакции ---
            after_rows = await conn.fetch(
                "SELECT id, course_id, difficulty_id FROM tasks "
                "WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            bad = [
                f"id={r['id']}: ожидали {expected_after[r['id']]}, факт {r['difficulty_id']}"
                for r in after_rows if r["difficulty_id"] != expected_after[r["id"]]
            ]
            if len(after_rows) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for b in bad[:20]:
                    print(f"  - {b}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after_rows)}/{len(ids)} совпали с планом — OK")

            hard_in_base = await conn.fetchval(f"""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id = {HARD}
                  AND course_id BETWEEN {BASE_MIN} AND {BASE_MAX}
            """)
            soft_in_block = await conn.fetchval(f"""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id <> {HARD}
                  AND course_id BETWEEN {BLOCK_MIN} AND {BLOCK_MAX}
            """)
            if hard_in_base or soft_in_block:
                print(
                    f"\nОШИБКА инварианта блока «Сложные»: HARD в базовых {hard_in_base}, "
                    f"не-HARD в блоке {soft_in_block} — ROLLBACK"
                )
                await tx.rollback()
                return 1
            print("инварианты блока «Сложные» по всему проду — OK (0 нарушений)")

            dupes = await conn.fetch("""
                SELECT course_id, order_position, COUNT(*) AS n
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id, order_position HAVING COUNT(*) > 1
            """, courses)
            if dupes:
                print(f"\nОШИБКА: коллизии order_position: {len(dupes)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален внутри course_id — OK (0 коллизий)")

            violations = await conn.fetch("""
                SELECT course_id FROM (
                    SELECT course_id, order_position, difficulty_id,
                        LAG(difficulty_id) OVER (
                            PARTITION BY course_id ORDER BY order_position ASC NULLS LAST
                        ) AS prev_difficulty
                    FROM tasks WHERE course_id = ANY($1::int[])
                ) x
                WHERE prev_difficulty IS NOT NULL AND difficulty_id < prev_difficulty
                GROUP BY course_id
            """, courses)
            if violations:
                print(f"\nОШИБКА: межгрупповые нарушения порядка в {len(violations)} курсах — ROLLBACK")
                await tx.rollback()
                return 1
            print("межгрупповой порядок THEORY->EASY->NORMAL->HARD->PROJECT — OK (0 нарушений)")

            gaps = await conn.fetch("""
                SELECT course_id, COUNT(*) AS n_tasks, MIN(order_position) AS min_op,
                       MAX(order_position) AS max_op
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id
                HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, courses)
            if gaps:
                print(f"\nОШИБКА: order_position не плотный 1..N в {len(gaps)} курсах — ROLLBACK")
                for g in gaps:
                    print(f"  {dict(g)}")
                await tx.rollback()
                return 1
            print("order_position плотный 1..N во всех затронутых курсах — OK")

            if apply:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                print("\nROLLBACK — dry-run, изменения откатаны.")
        except Exception:
            await tx.rollback()
            raise
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply, plan_path=Path(args.plan))))
