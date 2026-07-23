"""tsk-381 п.3 — оценка сложности Яндекс.Учебника (канон 3).

Градация Яндекса не лежит ни в тексте задания, ни в нашем импорте
(`difficulty_code` пуст у всех 7). Достали её так:

`external_uid` вида `ext:calib:yandex:tier1:<дата>:<uuid>:<N>` оказался прямым
адресом страницы тренажёра — `/ege/inf/collections/<uuid>/task/<N>`. На этой
странице уровень напечатан явно: «Уровень сложности: Проще ЕГЭ / Простая /
Средняя / Сложная / Сложнее ЕГЭ». Авторизованный API `POST /api/v5/gpttr` для
этого не нужен вовсе (CSRF-токен там выдаёт `/api/v5/get-csrf-token` полем `sk`,
но форма тела запроса другая — «no output deps fetched»).

Текст каждого задания сверен со страницей источника — совпадает дословно.

Свёртка пятиуровневой шкалы Яндекса в нашу трёхуровневую:
    Проще ЕГЭ, Простая        → EASY(2)
    Средняя                   → NORMAL(3)
    Сложная, Сложнее ЕГЭ      → HARD(4)

Задание 2991 не включено: его `external_uid` не содержит номера задания, а uuid
не открывается ни как подборка, ни как задание — источник для него не достался,
там остаётся оценка агента (канон 4).

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/apply_yandex_difficulty_tsk381.py
    PROD_DB_DSN=... python scripts/apply_yandex_difficulty_tsk381.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg

EASY, NORMAL, HARD = 2, 3, 4
DECIDED_AT = "2026-07-23"

YANDEX_TO_LEVEL = {
    "Проще ЕГЭ": EASY,
    "Простая": EASY,
    "Средняя": NORMAL,
    "Сложная": HARD,
    "Сложнее ЕГЭ": HARD,
}

# (task_id, подборка, номер задания, уровень Яндекса)
SOURCE: list[tuple[int, str, str, str]] = [
    (2988, "5a55834b-8221-4fe0-bdb9-f5b356188024", "19", "Средняя"),
    (2989, "a97d888a-5402-4044-bb08-35bcc66f9ec7", "19", "Проще ЕГЭ"),
    (2990, "a97d888a-5402-4044-bb08-35bcc66f9ec7", "13", "Проще ЕГЭ"),
    (2992, "a97d888a-5402-4044-bb08-35bcc66f9ec7", "17", "Проще ЕГЭ"),
    (2993, "b24b2dd9-52dc-42a7-b9f8-766c46e4c737", "25", "Простая"),
    (2994, "b24b2dd9-52dc-42a7-b9f8-766c46e4c737", "9", "Средняя"),
]

BLOCK_MIN, BLOCK_MAX = 1379, 1403


async def main(apply: bool) -> int:
    """Ставит уровень и обоснование канона 3 в одной транзакции."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-381: Яндекс, {len(SOURCE)} заданий — {mode} ===\n")

    ids = [s[0] for s in SOURCE]
    want = {s[0]: YANDEX_TO_LEVEL[s[3]] for s in SOURCE}

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, course_id, difficulty_id, difficulty_provenance, is_active "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        problems: list[str] = []
        if len(rows) != len(ids):
            problems.append(f"найдено {len(rows)} из {len(ids)}")
        for task_id, coll, num, level in SOURCE:
            row = by_id.get(task_id)
            if row is None:
                continue
            if coll not in (row["external_uid"] or ""):
                problems.append(f"id={task_id}: uuid подборки не найден в external_uid")
            if not row["is_active"]:
                problems.append(f"id={task_id}: неактивно")
            existing = row["difficulty_provenance"]
            existing = json.loads(existing) if isinstance(existing, str) else existing
            if existing is not None and int(existing.get("canon", 9)) < 3:
                problems.append(
                    f"id={task_id}: обосновано каноном {existing.get('canon')} — он сильнее источника"
                )
            in_block = BLOCK_MIN <= row["course_id"] <= BLOCK_MAX
            if (want[task_id] == HARD) != in_block:
                problems.append(
                    f"id={task_id}: уровень {want[task_id]} при курсе {row['course_id']} "
                    f"нарушает инвариант блока — нужен перенос, не этот скрипт"
                )
            print(
                f"  id={task_id} {row['external_uid'][:52]}… "
                f"уровень сейчас {row['difficulty_id']} -> источник «{level}» = {want[task_id]}"
            )

        if problems:
            print("\nОШИБКА, обновление не выполняется:")
            for line in problems:
                print(f"  - {line}")
            return 1

        changes = [i for i in ids if by_id[i]["difficulty_id"] != want[i]]
        courses = sorted({by_id[i]["course_id"] for i in ids})
        print(f"\nменяют уровень: {len(changes)} ({changes}); подтверждают: {len(ids) - len(changes)}")

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            for task_id, coll, num, level in SOURCE:
                provenance = {
                    "canon": 3, "source": "yandex",
                    "evidence": (
                        f"страница тренажёра, подборка {coll} задание {num}: "
                        f"«Уровень сложности: {level}»; текст сверен со страницей источника"
                    ),
                    "decided_at": DECIDED_AT, "task": "tsk-381",
                }
                await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1, difficulty_provenance = $2::jsonb WHERE id = $3",
                    YANDEX_TO_LEVEL[level], json.dumps(provenance, ensure_ascii=False), task_id,
                )

            reorder = await conn.execute("""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY course_id
                        ORDER BY difficulty_id ASC,
                            CASE task_content->>'type'
                                WHEN 'SC' THEN 1 WHEN 'MC' THEN 1
                                WHEN 'TA' THEN 2 WHEN 'SA' THEN 2
                                WHEN 'SA_COM' THEN 3 ELSE 99 END ASC,
                            order_position ASC NULLS LAST, id ASC
                    ) AS new_op
                    FROM tasks WHERE course_id = ANY($1::int[])
                )
                UPDATE tasks t SET order_position = n.new_op
                FROM new_order n WHERE t.id = n.id AND (t.order_position IS DISTINCT FROM n.new_op)
            """, courses)
            print(f"REORDER (курсы {courses}): {reorder}")

            after = await conn.fetch(
                "SELECT id, difficulty_id, difficulty_provenance FROM tasks WHERE id = ANY($1::int[])",
                ids,
            )
            bad: list[str] = []
            for row in after:
                value = row["difficulty_provenance"]
                value = json.loads(value) if isinstance(value, str) else value
                if row["difficulty_id"] != want[row["id"]]:
                    bad.append(f"id={row['id']}: уровень {row['difficulty_id']}, ожидали {want[row['id']]}")
                if not value or value.get("canon") != 3 or value.get("source") != "yandex":
                    bad.append(f"id={row['id']}: обоснование записано неверно")
            if len(after) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(ids)} — OK")

            hard_in_base = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id = 4
                  AND course_id BETWEEN 138 AND 165
            """)
            soft_in_block = await conn.fetchval("""
                SELECT count(*) FROM tasks WHERE is_active AND difficulty_id <> 4
                  AND course_id BETWEEN 1379 AND 1403
            """)
            if hard_in_base or soft_in_block:
                print(f"\nОШИБКА инварианта блока: {hard_in_base} / {soft_in_block} — ROLLBACK")
                await tx.rollback()
                return 1
            print("инварианты блока «Сложные» по всему проду — OK")

            dupes = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT course_id, order_position FROM tasks WHERE course_id = ANY($1::int[])
                    GROUP BY course_id, order_position HAVING COUNT(*) > 1) d
            """, courses)
            gaps = await conn.fetch("""
                SELECT course_id FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, courses)
            if dupes or gaps:
                print(f"\nОШИБКА порядка: коллизий {dupes}, курсов с дырами {len(gaps)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален и плотный 1..N — OK")

            by_canon = await conn.fetch("""
                SELECT (difficulty_provenance->>'canon')::int AS k, count(*) AS n
                FROM tasks WHERE is_active AND external_uid ILIKE '%yandex%'
                  AND difficulty_provenance IS NOT NULL GROUP BY 1 ORDER BY 1
            """)
            print("Яндекс по канонам: " + ", ".join(f"канон {r['k']} — {r['n']}" for r in by_canon))

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
