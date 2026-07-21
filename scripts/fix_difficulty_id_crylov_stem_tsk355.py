"""tsk-355 — точечный фикс difficulty_id для 2 заданий Крылова, где встроенная
в текст метка книги ("Уровень простой/средний/сложный") СОВПАДАЕТ с последней
меткой ТГ-разбора @cyberguru_ege, и обе расходятся с текущим difficulty_id —
тот же критерий двойного независимого согласия, что уже применялся в tsk-354
(docs/qa/2026-07-21-tsk354-difficulty-reconciliation.md, раздел "Применено").

Остальные 37 из 39 найденных расхождений (единственный источник — книга, или
трёхсторонний спор книга/ТГ) НЕ входят в этот скрипт — оставлены на решение
оператора (см. отчёт tsk-355).

DSN берётся ТОЛЬКО из переменной окружения PROD_DB_DSN (не хардкодится и не
пишется в файл) — формат asyncpg: postgresql://user:pass@host:5432/learn.

Запуск:
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_stem_tsk355.py            # dry-run
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_stem_tsk355.py --apply     # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# (task_id, course_id, expected_current_difficulty_id, new_difficulty_id, external_uid)
FIXES: list[tuple[int, int, int, int, str]] = [
    (9478, 155, 3, 2, "crylov:v1t4"),
    (9564, 152, 4, 2, "crylov:v16t25"),
]


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-355: fix difficulty_id (2 задания) — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        ids = [f[0] for f in FIXES]
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, order_position "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"Найдено {len(rows)} из {len(ids)} ожидаемых заданий.")

        mismatches = []
        for task_id, course_id, expected_before, _new, uid in FIXES:
            row = by_id.get(task_id)
            if row is None:
                mismatches.append(f"id={task_id}: не найдено в БД")
                continue
            if row["course_id"] != course_id or row["external_uid"] != uid:
                mismatches.append(
                    f"id={task_id}: course_id/external_uid не совпадают "
                    f"(ожидали course_id={course_id} uid={uid}, "
                    f"факт course_id={row['course_id']} uid={row['external_uid']})"
                )
            if row["difficulty_id"] != expected_before:
                mismatches.append(
                    f"id={task_id}: difficulty_id уже не {expected_before} "
                    f"(факт {row['difficulty_id']}) — кто-то изменил параллельно, СТОП"
                )
            print(f"  BEFORE id={task_id}: {dict(row)}")

        if mismatches:
            print("\nОШИБКА, обновление не выполняется:")
            for m in mismatches:
                print(f"  - {m}")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            for task_id, course_id, expected_before, new_difficulty, uid in FIXES:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1 "
                    "WHERE id = $2 AND difficulty_id = $3",
                    new_difficulty, task_id, expected_before,
                )
                print(f"UPDATE id={task_id}: {result}")

            after_rows = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id, order_position "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")

            expected_new = {f[0]: f[3] for f in FIXES}
            bad = [r for r in after_rows if r["difficulty_id"] != expected_new[r["id"]]]
            if bad:
                print(f"\nОШИБКА: верификация после UPDATE не сошлась ({len(bad)}) — ROLLBACK")
                await tx.rollback()
                return 1

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
    args = ap.parse_args()
    exit_code = asyncio.run(main(apply=args.apply))
    raise SystemExit(exit_code)
