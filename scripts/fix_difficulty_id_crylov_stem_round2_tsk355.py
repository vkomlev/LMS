"""tsk-355 — второй пакет фиксов difficulty_id для заданий Крылова, после того
как первый проход (fix_difficulty_id_crylov_stem_tsk355.py) сопоставлял ТГ по
паре (вариант, номер) без сверки ТЕКСТА и это дало минимум один ложный матч
(в1 з17 — под одной меткой оказались два разных условия). Этот пакет проверен
по полному тексту: для каждого задания вытянут пост(ы) канала @cyberguru_ege,
содержащие ДОСЛОВНО совпадающие числа/формулировки условия, а не только
вариант+номер.

Состав пакета (14 заданий), по итогам ручной сверки оператором:

Группа A — книга и ТГ совпадают между собой, обе против БД (8):
  9485, 4550, 9482, 9486, 9492, 9494, 9510, 9495

Группа B — книга и ТГ расходятся, оператор согласовал по значению ТГ (6,
только те где БД != ТГ; ещё 4 из группы B — 9479, 9503, 9556, 4561 — не
изменены, там БД уже совпадает с ТГ):
  9489, 9562, 9518, 9519, 9520, 9493

Правило по 9519/9520 (курс 147, "Задание 19-21"): один пост канала (#1061,
external_id=1061) решает все три подзадания единым способом и содержит
единственную метку уровня "Уровень простой" в начале — оператор подтвердил,
что эта метка относится ко всем трём подзадачам (9518/9519/9520), не только
к первой.

DSN — только через PROD_DB_DSN (не хардкодится). Запуск:
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_stem_round2_tsk355.py            # dry-run
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_stem_round2_tsk355.py --apply     # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# (task_id, course_id, expected_current_difficulty_id, new_difficulty_id, external_uid, источник)
FIXES: list[tuple[int, int, int, int, str, str]] = [
    # Группа A — книга = ТГ
    (9485, 139, 3, 2, "crylov:v1t13", "ТГ #890, книга=простой"),
    (4550, 140, 3, 2, "crylov:v1t1", "ТГ #928, книга=простой"),
    (9482, 141, 3, 2, "crylov:v1t10", "ТГ #889, книга=простой"),
    (9486, 142, 3, 2, "crylov:v1t14", "ТГ #892, книга=простой"),
    (9492, 149, 4, 3, "crylov:v1t22", "ТГ #893, книга=средний"),
    (9494, 151, 4, 3, "crylov:v1t24", "ТГ #769, книга=средний"),
    (9510, 151, 4, 3, "crylov:v5t24", "ТГ #799/#800, книга=средний"),
    (9495, 152, 4, 3, "crylov:v1t25", "ТГ #770, книга=средний"),
    # Группа B — оператор согласовал по ТГ (книга расходится)
    (9489, 146, 4, 2, "crylov:v1t18", "ТГ #783 (+ранее найденный)=простой, книга=средний"),
    (9562, 146, 4, 2, "crylov:v16t18", "ТГ #1040=простой, книга=средний"),
    (9518, 147, 4, 2, "crylov:v11t19", "ТГ #1061=простой, книга=средний"),
    (9519, 147, 4, 2, "crylov:v11t20", "правило 19-21: тот же пост #1061"),
    (9520, 147, 4, 2, "crylov:v11t21", "правило 19-21: тот же пост #1061"),
    (9493, 150, 4, 3, "crylov:v1t23", "ТГ #768=средний, книга=простой"),
]


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-355 round2: fix difficulty_id ({len(FIXES)} заданий) — {mode} ===\n")

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
        for task_id, course_id, expected_before, _new, uid, source in FIXES:
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
            print(f"  BEFORE id={task_id} ({source}): {dict(row)}")

        if mismatches:
            print("\nОШИБКА, обновление не выполняется:")
            for m in mismatches:
                print(f"  - {m}")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            for task_id, course_id, expected_before, new_difficulty, uid, source in FIXES:
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
