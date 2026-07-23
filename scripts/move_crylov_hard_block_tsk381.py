"""tsk-381 — перенос 7 заданий Крылова между базовым курсом и блоком «Сложные».

Вердикт оператора 2026-07-23: переносим.

В проде держится инвариант **`difficulty_id = 4` ⟺ курс входит в блок «Сложные»
(1379-1403)**: в базовых курсах 138-165 ноль заданий HARD, в блоке — только HARD и
только `requirement_level = 'recommended'` (tsk-347). Поэтому у этих 7 заданий сменить
один `difficulty_id` нельзя — задание осталось бы в курсе, которому не соответствует.
Меняются три поля разом: уровень, курс, уровень обязательности.

Канон уровня — авторская разметка «Уровень …» в постах канала @cyberguru_ege, текст
задания сверен с текстом поста. Разбор: docs/qa/2026-07-23-tsk381-difficulty-crylov-tg-canon.md

Последствие для учеников (посчитано до правки, оператор предупреждён):
  - 9526 уходит из обязательного курса 157 в блок → 4 ученика, решивших его верно,
    теряют этот зачёт в 157 (сами результаты сохраняются: `task_results` привязан к
    `task_id`, а не к курсу);
  - 9553 приходит из блока в обязательный курс 144 → 2 ученика, наоборот, получают зачёт.

Реордер: прямая запись идёт мимо `TasksService.bulk_upsert`, durable-хук tsk-345 не
сработает — реордер вызывается здесь той же ROW_NUMBER-логикой для ВСЕХ 10 затронутых
курсов (и откуда унесли, и куда принесли). Триггер `trg_set_task_order_position` глушится
session-variable `app.skip_task_order_trigger` (is_local=true), НЕ через `ALTER TABLE ...
DISABLE TRIGGER`: последнее берёт ACCESS EXCLUSIVE лок на всю `tasks` и блокирует живой
трафик учеников (урок tsk-345/346).

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/move_crylov_hard_block_tsk381.py
    PROD_DB_DSN=... python scripts/move_crylov_hard_block_tsk381.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

BLOCK_MIN, BLOCK_MAX = 1379, 1403
BASE_MIN, BASE_MAX = 138, 165
HARD = 4

# (task_id, uid, курс «до», уровень «до», курс «после», уровень «после», req «после», пост)
MOVES: list[tuple[int, str, int, int, int, int, str, str]] = [
    (9496, "crylov:v1t26", 1402, 4, 153, 3, "required", "744"),
    (9517, "crylov:v11t18", 1396, 4, 146, 2, "required", "1039"),
    (9526, "crylov:v16t6", 157, 3, 1384, 4, "recommended", "1006"),
    (9533, "crylov:v16t27", 1403, 4, 154, 3, "required", "1073"),
    (9553, "crylov:v5t16", 1394, 4, 144, 3, "required", "1038"),
    (9554, "crylov:v5t26", 1402, 4, 153, 3, "required", "802"),
    (9565, "crylov:v16t26", 1402, 4, 153, 3, "required", "837"),
]

# Контрольные задания, которые НЕ должны измениться: task_id -> (course_id, difficulty_id).
# Соседи по заданию 18 (вердикт tsk-355) и задания, поправленные первым проходом tsk-381.
UNTOUCHED: dict[int, tuple[int, int]] = {
    9489: (146, 2), 9562: (146, 2), 9518: (147, 2),
    9480: (158, 2), 9558: (140, 3), 9557: (162, 2),
    3759: (158, 2),
}

COURSES: list[int] = sorted({m[2] for m in MOVES} | {m[4] for m in MOVES})

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
    """Переносит задания в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-381: перенос {len(MOVES)} заданий, курсы {COURSES} — {mode} ===\n")

    ids = [m[0] for m in MOVES]
    control_ids = sorted(UNTOUCHED)
    expected = {m[0]: (m[4], m[5], m[6]) for m in MOVES}

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, requirement_level, order_position "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"BEFORE: найдено {len(rows)} из {len(ids)} ожидаемых заданий.")

        problems: list[str] = []
        for task_id, uid, course_before, diff_before, course_after, diff_after, req, post in MOVES:
            row = by_id.get(task_id)
            if row is None:
                problems.append(f"id={task_id}: не найдено в БД")
                continue
            if row["external_uid"] != uid:
                problems.append(
                    f"id={task_id}: external_uid не совпадает "
                    f"(ожидали {uid}, факт {row['external_uid']})"
                )
            if row["course_id"] != course_before or row["difficulty_id"] != diff_before:
                problems.append(
                    f"id={task_id}: состояние «до» не совпадает (ожидали курс "
                    f"{course_before}/уровень {diff_before}, факт {row['course_id']}/"
                    f"{row['difficulty_id']}) — кто-то изменил параллельно, СТОП"
                )
            print(
                f"  BEFORE id={task_id}: курс {course_before} -> {course_after}, "
                f"уровень {diff_before} -> {diff_after}, req -> {req} (пост {post}) {dict(row)}"
            )

        control_before = await conn.fetch(
            "SELECT id, course_id, difficulty_id, requirement_level, order_position FROM tasks "
            "WHERE id = ANY($1::int[]) ORDER BY id",
            control_ids,
        )
        for r in control_before:
            print(f"  BEFORE (контроль, не трогаем) id={r['id']}: {dict(r)}")
            if (r["course_id"], r["difficulty_id"]) != UNTOUCHED[r["id"]]:
                problems.append(
                    f"id={r['id']}: контрольное задание уже не "
                    f"{UNTOUCHED[r['id']]} (факт {r['course_id']}/{r['difficulty_id']}) — СТОП"
                )
        if len(control_before) != len(control_ids):
            problems.append("часть контрольных заданий не найдена в БД — СТОП")

        counts_before = {
            r["course_id"]: r["n"]
            for r in await conn.fetch(
                "SELECT course_id, count(*) AS n FROM tasks "
                "WHERE course_id = ANY($1::int[]) GROUP BY 1", COURSES,
            )
        }
        print(f"\nЗаданий в курсах до: {dict(sorted(counts_before.items()))}")

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

            for task_id, _uid, course_before, diff_before, course_after, diff_after, req, _post in MOVES:
                result = await conn.execute(
                    "UPDATE tasks SET course_id = $1, difficulty_id = $2, requirement_level = $3 "
                    "WHERE id = $4 AND course_id = $5 AND difficulty_id = $6",
                    course_after, diff_after, req, task_id, course_before, diff_before,
                )
                print(f"MOVE id={task_id}: {course_before}/{diff_before} -> {course_after}/{diff_after}: {result}")

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
                "SELECT id, course_id, external_uid, difficulty_id, requirement_level, order_position "
                "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            bad: list[str] = []
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")
                want = expected[r["id"]]
                got = (r["course_id"], r["difficulty_id"], r["requirement_level"])
                if got != want:
                    bad.append(f"id={r['id']}: ожидали {want}, факт {got}")
            if len(after_rows) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for b in bad:
                    print(f"  - {b}")
                await tx.rollback()
                return 1

            control_after = await conn.fetch(
                "SELECT id, course_id, difficulty_id, requirement_level FROM tasks "
                "WHERE id = ANY($1::int[]) ORDER BY id",
                control_ids,
            )
            control_bad = [
                r for r in control_after
                if (r["course_id"], r["difficulty_id"]) != UNTOUCHED[r["id"]]
            ]
            for r in control_after:
                print(f"  AFTER (контроль) id={r['id']}: {dict(r)}")
            if control_bad:
                print(f"\nОШИБКА: контрольные задания изменились ({len(control_bad)}) — ROLLBACK")
                await tx.rollback()
                return 1

            counts_after = {
                r["course_id"]: r["n"]
                for r in await conn.fetch(
                    "SELECT course_id, count(*) AS n FROM tasks "
                    "WHERE course_id = ANY($1::int[]) GROUP BY 1", COURSES,
                )
            }
            print(f"\nЗаданий в курсах после: {dict(sorted(counts_after.items()))}")
            delta_expected: dict[int, int] = {}
            for _t, _u, c_before, _d1, c_after, _d2, _r, _p in MOVES:
                delta_expected[c_before] = delta_expected.get(c_before, 0) - 1
                delta_expected[c_after] = delta_expected.get(c_after, 0) + 1
            for course, delta in sorted(delta_expected.items()):
                fact = counts_after.get(course, 0) - counts_before.get(course, 0)
                if fact != delta:
                    bad.append(f"курс {course}: ожидали изменение {delta:+d}, факт {fact:+d}")
            if bad:
                print("\nОШИБКА: состав курсов изменился не так, как задумано — ROLLBACK")
                for b in bad:
                    print(f"  - {b}")
                await tx.rollback()
                return 1
            print("состав курсов изменился ровно на запланированные переносы — OK")

            # --- Инварианты по всему проду, не только по затронутым курсам ---
            hard_in_base = await conn.fetch(f"""
                SELECT id, course_id FROM tasks
                WHERE is_active AND difficulty_id = {HARD}
                  AND course_id BETWEEN {BASE_MIN} AND {BASE_MAX}
            """)
            soft_in_block = await conn.fetch(f"""
                SELECT id, course_id, difficulty_id FROM tasks
                WHERE is_active AND difficulty_id <> {HARD}
                  AND course_id BETWEEN {BLOCK_MIN} AND {BLOCK_MAX}
            """)
            required_in_block = await conn.fetch(f"""
                SELECT id, course_id, requirement_level FROM tasks
                WHERE is_active AND requirement_level <> 'recommended'
                  AND course_id BETWEEN {BLOCK_MIN} AND {BLOCK_MAX}
            """)
            for label, rows_bad in (
                ("HARD в базовом курсе", hard_in_base),
                ("не-HARD в блоке «Сложные»", soft_in_block),
                ("обязательное задание в блоке «Сложные»", required_in_block),
            ):
                if rows_bad:
                    print(f"\nОШИБКА инварианта: {label} — {len(rows_bad)} шт., ROLLBACK")
                    for r in rows_bad[:10]:
                        print(f"  {dict(r)}")
                    await tx.rollback()
                    return 1
            print("инварианты блока «Сложные» по всему проду — OK (0 нарушений)")

            dupes = await conn.fetch("""
                SELECT course_id, order_position, COUNT(*) AS n
                FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id, order_position HAVING COUNT(*) > 1
            """, COURSES)
            if dupes:
                print(f"\nОШИБКА: коллизии order_position: {len(dupes)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален внутри course_id — OK (0 коллизий)")

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
