"""tsk-381 — применение экспертных оценок агента (канон 4).

Канон 4 — самый слабый: это оценка агента по тексту задания, а не разметка
автора и не вердикт оператора. Любой канон выше её перебивает. Правило оценки
(калибровка с оператором 2026-07-23): уровень задаёт ПРИЁМ, а не объём счёта —
знакомый шаблон → лёгкий, шаблон с подвохом → средний, нужен свой алгоритм →
сложный. Слепая сверка на 22 заданиях с известным каноном дала 16/22 до
уточнения правила; после уточнения все расхождения выправились.

Источник — `reviews/tsk381/agent-ratings-2026-07-23.json`. Применяются только
строки из `ratings`: списки `needs_course_move` (правка задевает инвариант
«difficulty_id = 4 ⟺ курс из блока Сложные») и `needs_full_text` остаются
оператору.

Пишутся ОБА поля: `difficulty_id` и `difficulty_provenance` — обоснование
всегда описывает то значение, которое ставится.

DSN — только через env var PROD_DB_DSN. Запуск:
    PROD_DB_DSN=... python scripts/apply_agent_ratings_tsk381.py
    PROD_DB_DSN=... python scripts/apply_agent_ratings_tsk381.py --apply
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
DEFAULT_RATINGS = _LMS_ROOT / "reviews" / "tsk381" / "agent-ratings-2026-07-23.json"

BLOCK_MIN, BLOCK_MAX = 1379, 1403
BASE_MIN, BASE_MAX = 138, 165
HARD = 4
DECIDED_AT = "2026-07-23"

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


def load_ratings(path: Path) -> list[dict[str, Any]]:
    """Оценки к применению из артефакта прогона."""
    return json.loads(path.read_text(encoding="utf-8"))["ratings"]


async def main(apply: bool, ratings_path: Path) -> int:
    """Ставит уровень и обоснование в одной транзакции с построчной верификацией."""
    dsn = os.environ.get("PROD_DB_DSN")
    if not dsn:
        print("ОШИБКА: переменная окружения PROD_DB_DSN не задана.")
        return 1

    ratings = load_ratings(ratings_path)
    want = {r["id"]: r for r in ratings}
    ids = sorted(want)
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-381 оценки агента: {len(ids)} заданий — {mode} ===")
    print(f"источник: {ratings_path}\n")

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT id, course_id, difficulty_id, difficulty_provenance, is_active "
            "FROM tasks WHERE id = ANY($1::int[])",
            ids,
        )
        by_id = {r["id"]: r for r in rows}
        problems: list[str] = []
        if len(rows) != len(ids):
            problems.append(f"в БД найдено {len(rows)} заданий из {len(ids)}")

        for task_id, rating in want.items():
            row = by_id.get(task_id)
            if row is None:
                continue
            if not row["is_active"]:
                problems.append(f"id={task_id}: задание неактивно")
            # Канон выше 4-го уже обосновал значение — не перетираем.
            existing = row["difficulty_provenance"]
            existing = json.loads(existing) if isinstance(existing, str) else existing
            if existing is not None and int(existing.get("canon", 9)) < 4:
                problems.append(
                    f"id={task_id}: уже обосновано каноном {existing.get('canon')} — "
                    f"оценка агента его не перебивает"
                )
            in_block = BLOCK_MIN <= row["course_id"] <= BLOCK_MAX
            if (rating["level"] == HARD) != in_block:
                problems.append(
                    f"id={task_id}: оценка {rating['level']} при курсе {row['course_id']} "
                    f"нарушает инвариант блока «Сложные» — нужен перенос, не этот скрипт"
                )

        if problems:
            print("ОШИБКА, обновление не выполняется:")
            for line in problems[:25]:
                print(f"  - {line}")
            return 1
        print("проверки до записи пройдены: активность, канон, инвариант блока — OK")

        changes = [i for i in ids if by_id[i]["difficulty_id"] != want[i]["level"]]
        print(f"меняют уровень: {len(changes)}; подтверждают текущий: {len(ids) - len(changes)}")
        courses = sorted({by_id[i]["course_id"] for i in ids})

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "SELECT set_config('app.skip_task_order_trigger', 'true', true)"
            )
            for task_id in ids:
                rating = want[task_id]
                provenance = {
                    "canon": 4, "source": "оценка агента",
                    "evidence": rating["why"],
                    "decided_at": DECIDED_AT, "task": "tsk-381",
                }
                await conn.execute(
                    "UPDATE tasks SET difficulty_id = $1, difficulty_provenance = $2::jsonb "
                    "WHERE id = $3",
                    rating["level"], json.dumps(provenance, ensure_ascii=False), task_id,
                )

            reorder = await conn.execute(f"""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                    FROM tasks WHERE course_id = ANY($1::int[])
                )
                UPDATE tasks t SET order_position = n.new_op
                FROM new_order n
                WHERE t.id = n.id AND (t.order_position IS DISTINCT FROM n.new_op)
            """, courses)
            print(f"REORDER (курсов {len(courses)}): {reorder}")

            after = await conn.fetch(
                "SELECT id, difficulty_id, difficulty_provenance FROM tasks "
                "WHERE id = ANY($1::int[])",
                ids,
            )
            bad: list[str] = []
            for row in after:
                rating = want[row["id"]]
                if row["difficulty_id"] != rating["level"]:
                    bad.append(f"id={row['id']}: уровень {row['difficulty_id']}, ожидали {rating['level']}")
                value = row["difficulty_provenance"]
                value = json.loads(value) if isinstance(value, str) else value
                if not value or value.get("canon") != 4 or value.get("evidence") != rating["why"]:
                    bad.append(f"id={row['id']}: обоснование записано неверно")
            if len(after) != len(ids):
                bad.append("после UPDATE найдены не все задания")
            if bad:
                print("\nОШИБКА построчной верификации — ROLLBACK:")
                for line in bad[:20]:
                    print(f"  - {line}")
                await tx.rollback()
                return 1
            print(f"построчная верификация: {len(after)}/{len(ids)} — OK")

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
                    f"\nОШИБКА инварианта блока: HARD в базовых {hard_in_base}, "
                    f"не-HARD в блоке {soft_in_block} — ROLLBACK"
                )
                await tx.rollback()
                return 1
            print("инвариант блока «Сложные» по всему проду — OK")

            dupes = await conn.fetchval("""
                SELECT count(*) FROM (
                    SELECT course_id, order_position FROM tasks
                    WHERE course_id = ANY($1::int[])
                    GROUP BY course_id, order_position HAVING COUNT(*) > 1) d
            """, courses)
            gaps = await conn.fetch("""
                SELECT course_id FROM tasks WHERE course_id = ANY($1::int[])
                GROUP BY course_id
                HAVING MIN(order_position) <> 1 OR MAX(order_position) <> COUNT(*)
            """, courses)
            if dupes or gaps:
                print(f"\nОШИБКА порядка: коллизий {dupes}, курсов с дырами {len(gaps)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("order_position уникален и плотный 1..N — OK")

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
    ap.add_argument("--ratings", default=str(DEFAULT_RATINGS))
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply, ratings_path=Path(args.ratings))))
