"""tsk-381 — difficulty_id партии Крылова по канону ТГ-разборов.

Канон (решение оператора 2026-07-23): авторская разметка «Уровень …» в постах
канала @cyberguru_ege. Правило «номер задания ЕГЭ → уровень» каноном НЕ является.

Скоуп этого прохода — 11 заданий, у которых правка не задевает инвариант
«difficulty_id = 4 ⟺ курс из блока "Сложные" (1379-1403)»: все переходы внутри
пары EASY(2) ↔ NORMAL(3), курс не меняется.

Ещё 7 расхождений (9496, 9517, 9526, 9533, 9553, 9554, 9565) требуют вместе с
difficulty_id переноса задания между базовым курсом и курсом блока «Сложные» —
вынесены на отдельное решение оператора, здесь НЕ трогаются и проверяются как
контрольные.

Провенанс каждой правки — пост канала, текст условия сверен с текстом поста
(порог 0.65, пограничные случаи просмотрены вручную). Полный разбор:
docs/qa/2026-07-23-tsk381-difficulty-crylov-tg-canon.md

Ни одно из заданий не пересекается с 42 вердиктами оператора из tsk-355.

Реордер: прямая запись идёт мимо `TasksService.bulk_upsert`, durable-хук tsk-345
не сработает — реордер вызывается здесь той же ROW_NUMBER-логикой. Триггер
`trg_set_task_order_position` глушится session-variable `app.skip_task_order_trigger`
(is_local=true), НЕ через `ALTER TABLE ... DISABLE TRIGGER`: последнее берёт
ACCESS EXCLUSIVE лок на всю `tasks` и блокирует живой трафик (урок tsk-345/346).

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_tg_canon_tsk381.py
    PROD_DB_DSN=... python scripts/fix_difficulty_id_crylov_tg_canon_tsk381.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

# (task_id, course_id, difficulty_id «до», difficulty_id «после», external_uid, пост ТГ)
FIXES: list[tuple[int, int, int, int, str, str]] = [
    (2836, 159, 2, 3, "ext:pdf:d4:pdf:crylov:v3:20260602:v3t8", "896"),
    (2837, 160, 2, 3, "ext:pdf:d4:pdf:crylov:v3:20260602:v3t9", "897"),
    (2839, 162, 2, 3, "ext:pdf:d4:pdf:crylov:v3:20260602:v3t11", "1023,907"),
    (9480, 158, 3, 2, "crylov:v1t7", "775"),
    (9527, 158, 3, 2, "crylov:v16t7", "1020"),
    (9529, 162, 3, 2, "crylov:v16t11", "1021"),
    (9550, 158, 3, 2, "crylov:v5t7", "899"),
    (9552, 160, 3, 2, "crylov:v5t9", "901"),
    (9557, 162, 3, 2, "crylov:v11t11", "812"),
    (9558, 140, 2, 3, "crylov:v16t1", "824"),
    (9559, 156, 3, 2, "crylov:v16t5", "826"),
]

# Задания, которые этот проход НЕ трогает: task_id -> difficulty_id.
# 7 расхождений с переносом между курсами + 3 соседних вердикта tsk-355 (задание 18
# вариантов 1 и 16 — они уже EASY, их значение не должно поехать).
UNTOUCHED: dict[int, int] = {
    9496: 4, 9517: 4, 9526: 3, 9533: 4, 9553: 4, 9554: 4, 9565: 4,
    9489: 2, 9562: 2, 9518: 2,
}

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
    """Применяет правки в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-381: {len(FIXES)} заданий по канону ТГ, курсы {COURSES} — {mode} ===\n")

    ids = [f[0] for f in FIXES]
    control_ids = sorted(UNTOUCHED)
    expected_after = {f[0]: f[3] for f in FIXES}

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
        for task_id, course_id, before, after, uid, post in FIXES:
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
            if row["difficulty_id"] != before:
                problems.append(
                    f"id={task_id}: difficulty_id уже не {before} "
                    f"(факт {row['difficulty_id']}) — кто-то изменил параллельно, СТОП"
                )
            print(f"  BEFORE id={task_id}: {before} -> {after} (пост {post}) {dict(row)}")

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
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )

            for task_id, _course_id, before, after, _uid, _post in FIXES:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1 WHERE id = $2 AND difficulty_id = $3",
                    after, task_id, before,
                )
                print(f"UPDATE difficulty id={task_id}: {before} -> {after}: {result}")

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

            # --- Построчная верификация внутри транзакции ---
            after_rows = await conn.fetch(
                "SELECT id, course_id, external_uid, difficulty_id, order_position "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            bad: list[str] = []
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")
                if r["difficulty_id"] != expected_after[r["id"]]:
                    bad.append(
                        f"id={r['id']}: ожидали {expected_after[r['id']]}, "
                        f"факт {r['difficulty_id']}"
                    )
            if len(after_rows) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for b in bad:
                    print(f"  - {b}")
                await tx.rollback()
                return 1

            control_after = await conn.fetch(
                "SELECT id, course_id, difficulty_id, order_position FROM tasks "
                "WHERE id = ANY($1::int[]) ORDER BY id",
                control_ids,
            )
            control_bad = [
                r for r in control_after if r["difficulty_id"] != UNTOUCHED[r["id"]]
            ]
            for r in control_after:
                print(f"  AFTER (контроль) id={r['id']}: {dict(r)}")
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

            # Инвариант блока «Сложные»: в базовых курсах не должно появиться HARD.
            hard_in_base = await conn.fetch("""
                SELECT id, course_id, difficulty_id FROM tasks
                WHERE course_id = ANY($1::int[]) AND difficulty_id = 4 AND is_active
            """, COURSES)
            if hard_in_base:
                print(
                    f"\nОШИБКА: HARD в базовом курсе ({len(hard_in_base)}) — "
                    f"нарушен инвариант блока «Сложные», ROLLBACK"
                )
                for r in hard_in_base:
                    print(f"  {dict(r)}")
                await tx.rollback()
                return 1
            print("инвариант «HARD только в блоке Сложные» — OK (0 нарушений)")

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
