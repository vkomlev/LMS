"""tsk-355 — курс 147 ("Задание 19-21 ЕГЭ по информатике. Теория игр"): оператор
указал на отдельную методическую особенность этого курса — 19/20/21 не отдельные
задания, а ОДНА игровая механика, которую канал @cyberguru_ege разбирает единым
видео/постом с ОДНОЙ меткой уровня. Уровень задания 19 применяется целиком к
20 и 21 того же варианта, а не берётся из (часто некорректной) метки,
встроенной в текст 20/21 по отдельности при импорте.

Раунд 2 уже применил это для варианта 11 (пост #1061 канала → EASY на 19/20/21).
Здесь — варианты 1, 5, 16, подтверждённые оператором вручную:
  - вариант 1: уровень простой. Обоснование оператора — задание на 1 кучу,
    без усложнений правил, простое уменьшение количества камней.
  - вариант 5: задание 19 этого варианта уже содержит собственную встроенную
    метку "Уровень простой" в тексте (расхождение было даже внутри одной
    записи — difficulty_id=HARD, а текст говорит "простой") — берём её.
  - вариант 16: задание 19 уже имеет difficulty_id=EASY и текстовую метку
    "простой" — берём как источник для 20/21 этого варианта (у которых
    difficulty_id разъехался на HARD).

Итог: 19/20/21 каждого варианта курса 147 получают ОДИНАКОВЫЙ difficulty_id.
Строки, где значение уже совпадает (19 у вариантов 1/16, 19/20/21 у варианта
11), в этот скрипт не включены — трогать нечего.

DSN — только через PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_1921_tsk355.py            # dry-run
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_1921_tsk355.py --apply     # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# (task_id, course_id, expected_current_difficulty_id, new_difficulty_id, external_uid, источник)
FIXES: list[tuple[int, int, int, int, str, str]] = [
    (9490, 147, 4, 2, "crylov:v1t20", "правило 19-21: в1 уровень простой (оператор)"),
    (9491, 147, 4, 2, "crylov:v1t21", "правило 19-21: в1 уровень простой (оператор)"),
    (9505, 147, 4, 2, "crylov:v5t19", "правило 19-21: собственная метка т19='простой'"),
    (9506, 147, 4, 2, "crylov:v5t20", "правило 19-21: та же метка, что т19 варианта 5"),
    (9507, 147, 4, 2, "crylov:v5t21", "правило 19-21: та же метка, что т19 варианта 5"),
    (9563, 147, 4, 2, "crylov:v16t20", "правило 19-21: метка т19 варианта 16='простой'"),
    (9531, 147, 4, 2, "crylov:v16t21", "правило 19-21: метка т19 варианта 16='простой'"),
]


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-355 (курс 147, правило 19-21): {len(FIXES)} заданий — {mode} ===\n")

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
