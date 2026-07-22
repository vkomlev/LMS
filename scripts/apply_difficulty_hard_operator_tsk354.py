"""tsk-354 — финальное применение: 8 заданий получают difficulty_id=4 (HARD)
по решению оператора (2026-07-22), плюс реордер order_position затронутых курсов.

Провенанс каждого задания (обоснование решения оператора):

| task_id | курс | обоснование                                                        |
|---------|------|--------------------------------------------------------------------|
| 2059    | 138  | ТГ-пост 1105 «Задание 3_7613 Поляков. Уровень сложный» (2026-06-22) |
| 2352    | 144  | ТГ-пост 1106 «Задание 16_48437 РешуЕГЭ. Уровень сложный» (2026-06-24)|
| 2116    | 139  | ТГ-пост 1108 «Задание 13_12451 КЕГЭ. Уровень сложный» (2026-06-24)  |
| 2386    | 154  | ТГ-пост 453 «Задание 27_70554. Уровень сложный» (2025-04-12)        |
| 2720    | 157  | ТГ-пост 1006 «Задание 6_16 Вариант 16 Крылова. Уровень сложный»     |
|         |      | (2026-06-10)                                                       |
| 2262    | 158  | WP-навигатор, блок «сложные» (ТГ-метки нет)                        |
| 3792    | 155  | WP-блок «сложные» + ручная проверка оператора 2026-07-22           |
| 3796    | 155  | WP-блок «сложные» + ручная проверка оператора 2026-07-22           |

НЕ трогаются (решение оператора, список закрыт):
- 3477 — остаётся HARD (все источники согласны, спор в отчёте был ложным:
  ТГ-метка сопоставлялась fuzzy-текстом и промахнулась, реальный пост 569
  говорит «Уровень сложный»);
- 3794 — остаётся NORMAL (ручная проверка оператора);
- 3759 — решается отдельно (ТГ-поста для него не существует вовсе).
Эти три задания проверяются скриптом как контрольные: если их difficulty_id
изменился — ROLLBACK.

Реордер: прямая запись идёт в обход `TasksService.bulk_upsert`, поэтому
durable-хук из tsk-345 не сработает — реордер вызывается здесь явно той же
ROW_NUMBER-логикой (THEORY→EASY→NORMAL→HARD→PROJECT, тайбрейк по текущему
order_position, чтобы не ломать ручные перестановки методистов).
Триггер `trg_set_task_order_position` глушится ТОЛЬКО session-variable
`app.skip_task_order_trigger` (is_local=true), НЕ через
`ALTER TABLE ... DISABLE TRIGGER` — последнее берёт ACCESS EXCLUSIVE лок на всю
таблицу `tasks` и блокирует живой трафик учеников (урок tsk-345/tsk-346).

DSN — только через env var PROD_DB_DSN, не хардкодится. Запуск:
    PROD_DB_DSN=... python scripts/apply_difficulty_hard_operator_tsk354.py           # dry-run
    PROD_DB_DSN=... python scripts/apply_difficulty_hard_operator_tsk354.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

HARD = 4

# (task_id, course_id, expected_current_difficulty_id, external_uid)
FIXES: list[tuple[int, int, int, str]] = [
    (2059, 138, 3, "ext:polyakov:pilot:mini50:7613"),
    (2116, 139, 3, "ext:d4:kompege:20260602:12451"),
    (2262, 158, 3, "ext:d4:sdamgia:20260602:29194"),
    (2352, 144, 3, "ext:d4:sdamgia:20260602:48437"),
    (2386, 154, 3, "ext:d4:sdamgia:20260602:70554"),
    (2720, 157, 2, "pdf:d4:pdf:crylov:v16:20260602:v16t6"),
    (3792, 155, 3, "wp_nav:4:f6c96838"),
    (3796, 155, 3, "wp_nav:4:d98ecbde"),
]

# Контрольные задания, которые НЕ должны измениться: task_id -> difficulty_id.
UNTOUCHED: dict[int, int] = {3477: 4, 3794: 3, 3759: 4}

COURSES: list[int] = sorted({f[1] for f in FIXES})

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


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-354 финал: {len(FIXES)} заданий -> HARD, реордер курсов {COURSES} — {mode} ===\n")

    ids = [f[0] for f in FIXES]
    control_ids = sorted(UNTOUCHED)

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
        for task_id, course_id, expected_before, uid in FIXES:
            row = by_id.get(task_id)
            if row is None:
                problems.append(f"id={task_id}: не найдено в БД")
                continue
            if row["course_id"] != course_id or row["external_uid"] != uid:
                problems.append(
                    f"id={task_id}: course_id/external_uid не совпадают "
                    f"(ожидали course_id={course_id} uid={uid}, "
                    f"факт course_id={row['course_id']} uid={row['external_uid']})"
                )
            if row["difficulty_id"] != expected_before:
                problems.append(
                    f"id={task_id}: difficulty_id уже не {expected_before} "
                    f"(факт {row['difficulty_id']}) — кто-то изменил параллельно, СТОП"
                )
            print(f"  BEFORE id={task_id}: {dict(row)}")

        control_before = await conn.fetch(
            "SELECT id, course_id, difficulty_id, order_position FROM tasks "
            "WHERE id = ANY($1::int[]) ORDER BY id",
            control_ids,
        )
        for r in control_before:
            print(f"  BEFORE (контроль, не трогаем) id={r['id']}: {dict(r)}")
            if r["difficulty_id"] != UNTOUCHED[r["id"]]:
                problems.append(
                    f"id={r['id']}: контрольное задание уже не "
                    f"difficulty_id={UNTOUCHED[r['id']]} (факт {r['difficulty_id']}) — СТОП"
                )
        if len(control_before) != len(control_ids):
            problems.append("часть контрольных заданий не найдена в БД — СТОП")

        if problems:
            print("\nОШИБКА, обновление не выполняется:")
            for p in problems:
                print(f"  - {p}")
            return 1

        tx = conn.transaction()
        await tx.start()
        try:
            # Глушим trg_set_task_order_position на время транзакции (is_local=true).
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )

            for task_id, _course_id, expected_before, _uid in FIXES:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1 WHERE id = $2 AND difficulty_id = $3",
                    HARD, task_id, expected_before,
                )
                print(f"UPDATE difficulty id={task_id}: {result}")

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
            """, COURSES)
            print(f"\nREORDER (курсы {COURSES}): {reorder}")

            # --- Верификация внутри транзакции ---
            after_rows = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id, order_position "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")
            bad = [r for r in after_rows if r["difficulty_id"] != HARD]
            if bad:
                print(f"\nОШИБКА: не все задания стали HARD ({len(bad)}) — ROLLBACK")
                await tx.rollback()
                return 1

            control_after = await conn.fetch(
                "SELECT id, course_id, difficulty_id, order_position FROM tasks "
                "WHERE id = ANY($1::int[]) ORDER BY id",
                control_ids,
            )
            for r in control_after:
                print(f"  AFTER (контроль) id={r['id']}: {dict(r)}")
            control_bad = [
                r for r in control_after if r["difficulty_id"] != UNTOUCHED[r["id"]]
            ]
            if control_bad:
                print(
                    f"\nОШИБКА: контрольные задания изменили difficulty_id "
                    f"({len(control_bad)}) — ROLLBACK"
                )
                await tx.rollback()
                return 1

            dupes = await conn.fetch("""
                SELECT course_id, order_position, COUNT(*) AS n
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id, order_position HAVING COUNT(*) > 1
            """, COURSES)
            if dupes:
                print(f"\nОШИБКА: коллизии order_position: {len(dupes)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("\norder_position уникален внутри course_id — OK (0 коллизий)")

            violations = await conn.fetch("""
                SELECT course_id, COUNT(*) AS n FROM (
                    SELECT course_id, order_position, difficulty_id,
                        LAG(difficulty_id) OVER (
                            PARTITION BY course_id ORDER BY order_position ASC NULLS LAST
                        ) AS prev_difficulty
                    FROM tasks WHERE course_id = ANY($1::int[])
                ) x
                WHERE prev_difficulty IS NOT NULL AND difficulty_id < prev_difficulty
                GROUP BY course_id
            """, COURSES)
            if violations:
                print(
                    f"\nОШИБКА: межгрупповые нарушения порядка в {len(violations)} "
                    f"курсах — ROLLBACK"
                )
                await tx.rollback()
                return 1
            print("межгрупповой порядок THEORY->EASY->NORMAL->HARD->PROJECT — OK (0 нарушений)")

            gaps = await conn.fetch("""
                SELECT course_id, COUNT(*) AS n_tasks, MIN(order_position) AS min_op,
                       MAX(order_position) AS max_op
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id
                HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, COURSES)
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
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply)))
