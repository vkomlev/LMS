"""tsk-355 — откат неавторизованной части раунда 3 (fix_difficulty_id_crylov_1921_tsk355.py).

Оператор подтвердил уровень «простой» ТОЛЬКО для варианта 1 курса 147
(обоснование: 1 куча, без усложнений правил, простое уменьшение). Агент
ошибочно распространил это решение на варианты 5 и 16 без подтверждения,
сославшись на встроенную в текст книжную метку — тот самый источник, который
весь разбор tsk-355 признал ненадёжным. Дополнительно выяснилось, что
вариант 16 структурно ДРУГАЯ игра (две кучи вместо одной, механика
«добавить»/«удвоить» вместо «убрать»/«разделить») — экстраполяция была не
только неавторизована, но и содержательно неверна.

Откатывает 5 из 7 правок round3 обратно в HARD(4):
  - вариант 5: 9505 (т19), 9506 (т20), 9507 (т21)
  - вариант 16: 9563 (т20), 9531 (т21)

НЕ трогает: 9490/9491 (вариант 1, подтверждено оператором явно) и
9518/9519/9520 (вариант 11, раунд 2, источник — реальный пост ТГ #1061).

DSN — только через PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/rollback_difficulty_id_crylov_1921_v5v16_tsk355.py            # dry-run
    PROD_DB_DSN=... python scripts/rollback_difficulty_id_crylov_1921_v5v16_tsk355.py --apply     # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# (task_id, course_id, expected_current_difficulty_id, restore_to_difficulty_id, external_uid)
ROLLBACKS: list[tuple[int, int, int, int, str]] = [
    (9505, 147, 2, 4, "crylov:v5t19"),
    (9506, 147, 2, 4, "crylov:v5t20"),
    (9507, 147, 2, 4, "crylov:v5t21"),
    (9563, 147, 2, 4, "crylov:v16t20"),
    (9531, 147, 2, 4, "crylov:v16t21"),
]


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-355: откат неавторизованных правок в5/в16 ({len(ROLLBACKS)}) — {mode} ===\n")

    conn = await asyncpg.connect(dsn)
    try:
        ids = [r[0] for r in ROLLBACKS]
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, order_position "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"Найдено {len(rows)} из {len(ids)} ожидаемых заданий.")

        mismatches = []
        for task_id, course_id, expected_before, _restore, uid in ROLLBACKS:
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
            print("\nОШИБКА, откат не выполняется:")
            for m in mismatches:
                print(f"  - {m}")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            for task_id, course_id, expected_before, restore_to, uid in ROLLBACKS:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1 "
                    "WHERE id = $2 AND difficulty_id = $3",
                    restore_to, task_id, expected_before,
                )
                print(f"UPDATE id={task_id}: {result}")

            after_rows = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id, order_position "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")

            expected_new = {r[0]: r[3] for r in ROLLBACKS}
            bad = [r for r in after_rows if r["difficulty_id"] != expected_new[r["id"]]]
            if bad:
                print(f"\nОШИБКА: верификация после отката не сошлась ({len(bad)}) — ROLLBACK")
                await tx.rollback()
                return 1

            if apply:
                await tx.commit()
                print("\nCOMMIT — откат сохранён.")
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
