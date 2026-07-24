"""tsk-354 — распространение канона 1 (ТГ-разборы) на `wp_nav` и `pdf:...crylov`.

Решение оператора (2026-07-24, вариант «А» из отчёта
`docs/qa/2026-07-24-tsk354-recheck-post-tsk381.md`): 15 заданий, для которых
переверификация точным совпадением текста нашла надёжный, единственный пост
канала @cyberguru_ege, приводятся к канону 1 — тем же порядком, что применялся
в tsk-381 (`fix_difficulty_id_crylov_tg_canon_tsk381.py`), но для партий,
которые tsk-381 явно оставила вне охвата (`wp_nav`, `pdf:...crylov` — не путать
с уже закрытой в tsk-355 партией `crylov:`).

Отбор постов — по точному совпадению текста (не fuzzy): для `wp_nav` сверен
дословно вопрос из условия LMS с постом; для `pdf:...crylov` — по уникальному
ключу (вариант, номер задания) без коллизий + построчная сверка текста. Полный
разбор с провенансом по каждому заданию — в отчёте выше.

Два задания НЕ входят в эту правку по итогам переверификации:
- 3765 — найденный пост отвечает на другой подвопрос той же игровой связки
  «Задание 19-21», доказательств для этого конкретного задания нет;
- 3475 — уже верно (EASY), дубль задания 2993, решённого в tsk-381.

Реордер: прямая запись идёт мимо `TasksService.bulk_upsert`, durable-хук tsk-345
не сработает — реордер вызывается той же ROW_NUMBER-логикой, что в tsk-354/381.
Триггер `trg_set_task_order_position` глушится session-variable
`app.skip_task_order_trigger` (is_local=true), НЕ через
`ALTER TABLE ... DISABLE TRIGGER` (ACCESS EXCLUSIVE лок на всю `tasks`,
урок tsk-345/346).

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/apply_wp_crylov_tg_canon_tsk354.py           # dry-run
    PROD_DB_DSN=... python scripts/apply_wp_crylov_tg_canon_tsk354.py --apply   # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

TASK = "tsk-354"
TG_SOURCE = "tg:cyberguru_ege"

# (task_id, course_id, external_uid, before, after, evidence)
FIXES: list[tuple[int, int, str, int, int, str]] = [
    (3779, 153, "wp_nav:26:9fe21449", 3, 2,
     "пост 851: легкий (2026-03-10), текст задания сверен дословно"),
    (3783, 153, "wp_nav:26:820c3087", 2, 3,
     "пост 939: средний (2026-04-27, позже поста 818 от 2026-02-18 — победил "
     "более поздний разбор), текст сверен дословно"),
    (3799, 153, "wp_nav:26:7d518f7a", 2, 3,
     "пост 853: средний (2026-03-11), текст задания сверен дословно"),
    (3921, 158, "wp_nav:7:87f36239", 2, 3,
     "пост 859: средний (2026-03-18), текст задания сверен дословно"),
    (4206, 147, "wp_nav:19:26d847b4", 2, 3,
     "пост 793: средний (2026-02-05), текст вопроса 19 совпадает с условием "
     "дословно (не просто тот же игровой сценарий)"),
    (2388, 148, "pdf:d4:pdf:crylov:v1:20260602:v1t2", 2, 3,
     "пост 879: средний (2026-04-07), вариант 1 задание 2, ключ уникален, "
     "текст сверен дословно"),
    (2392, 157, "pdf:d4:pdf:crylov:v1:20260602:v1t6", 2, 3,
     "пост 1005: средний/опечатка «срелний» (2026-06-10), вариант 1 задание 6, "
     "ключ уникален, текст сверен дословно"),
    (2401, 143, "pdf:d4:pdf:crylov:v1:20260602:v1t15", 2, 3,
     "пост 743: средний (2025-12-17), вариант 1 задание 15, ключ уникален, "
     "текст сверен дословно"),
    (2412, 153, "pdf:d4:pdf:crylov:v1:20260602:v1t26", 2, 3,
     "пост 744: средний (2025-12-18), вариант 1 задание 26, ключ уникален, "
     "текст сверен дословно"),
    (2484, 141, "pdf:d4:pdf:crylov:v5:20260602:v5t10", 2, 3,
     "пост 784: средний (2026-01-28), вариант 5 задание 10, ключ уникален, "
     "текст сверен дословно"),
    (2490, 144, "pdf:d4:pdf:crylov:v5:20260602:v5t16", 2, 3,
     "пост 1038: средний (2026-06-14), вариант 5 задание 16, ключ уникален, "
     "текст сверен дословно"),
    (2604, 159, "pdf:d4:pdf:crylov:v11:20260602:v11t8", 2, 3,
     "пост 1018: средний (2026-06-12), вариант 11 задание 8, ключ уникален, "
     "текст сверен дословно"),
    (2605, 160, "pdf:d4:pdf:crylov:v11:20260602:v11t9", 2, 3,
     "пост 881: средний (2026-04-07), вариант 11 задание 9, ключ уникален, "
     "текст сверен дословно"),
    (2718, 138, "pdf:d4:pdf:crylov:v16:20260602:v16t3", 2, 3,
     "пост 1000: средний (2026-06-09), вариант 16 задание 3, ключ уникален, "
     "текст сверен дословно"),
    (2735, 154, "pdf:d4:pdf:crylov:v16:20260602:v16t27", 2, 3,
     "пост 1073: средний (2026-06-16), вариант 16 задание 27, ключ уникален, "
     "текст сверен дословно"),
]

# Контрольные задания, которые НЕ трогаем (проверены как соседние по итогам
# переверификации): 3765 недостаточно доказательств, 3475 уже верно.
UNTOUCHED: dict[int, int] = {3765: 3, 3475: 2}

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


def _provenance(evidence: str) -> str:
    return json.dumps(
        {"canon": 1, "source": TG_SOURCE, "evidence": evidence,
         "decided_at": "2026-07-24", "task": TASK},
        ensure_ascii=False,
    )


async def main(apply: bool) -> int:
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-354 канон 1 -> wp_nav/pdf:crylov: {len(FIXES)} заданий, "
          f"курсы {COURSES} — {mode} ===\n")

    ids = [f[0] for f in FIXES]
    control_ids = sorted(UNTOUCHED)
    expected_after = {f[0]: f[4] for f in FIXES}

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, order_position, "
            "difficulty_provenance FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        print(f"BEFORE: найдено {len(rows)} из {len(ids)} ожидаемых заданий.")

        problems: list[str] = []
        for task_id, course_id, uid, before, after, evidence in FIXES:
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
            if row["difficulty_provenance"] is not None:
                problems.append(
                    f"id={task_id}: difficulty_provenance уже заполнен "
                    f"({row['difficulty_provenance']}) — СТОП, разобраться вручную"
                )
            print(f"  BEFORE id={task_id}: {before} -> {after} ({evidence[:60]}...) {dict(row)}")

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

            for task_id, _course_id, _uid, before, after, evidence in FIXES:
                result = await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1, difficulty_provenance = $2::jsonb "
                    "WHERE id = $3 AND difficulty_id = $4",
                    after, _provenance(evidence), task_id, before,
                )
                print(f"UPDATE id={task_id}: {before} -> {after}: {result}")

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
                "SELECT id, course_id, external_uid, difficulty_id, order_position, "
                "difficulty_provenance FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                ids,
            )
            bad: list[str] = []
            for r in after_rows:
                print(f"  AFTER id={r['id']}: {dict(r)}")
                if r["difficulty_id"] != expected_after[r["id"]]:
                    bad.append(
                        f"id={r['id']}: ожидали difficulty_id={expected_after[r['id']]}, "
                        f"факт {r['difficulty_id']}"
                    )
                prov = r["difficulty_provenance"]
                prov = json.loads(prov) if isinstance(prov, str) else prov
                if not prov or prov.get("canon") != 1 or prov.get("source") != TG_SOURCE:
                    bad.append(f"id={r['id']}: difficulty_provenance не записан как ожидалось: {prov}")
            if len(after_rows) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for b in bad:
                    print(f"  - {b}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after_rows)}/{len(ids)} совпали — OK")

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

            hard_in_base = await conn.fetch("""
                SELECT id, course_id, difficulty_id FROM tasks
                WHERE course_id = ANY($1::int[]) AND difficulty_id = 4 AND is_active
            """, COURSES)
            if hard_in_base:
                print(
                    f"\nОШИБКА: HARD в базовом курсе ({len(hard_in_base)}) — "
                    f"нарушен инвариант блока «Сложные», ROLLBACK"
                )
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
