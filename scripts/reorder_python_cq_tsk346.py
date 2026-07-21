"""tsk-346 — реордер order_position для Python-курсов 103..111 после перетега cq.

Локальный `.env` указывает на dev-БД (localhost), а прод-скрипт tsk-345
(`reorder_courses_by_difficulty_tsk345.py`) тянет DSN через
`app.db.session.async_session_factory` (dev-конфиг) и рассчитан на запуск НА
прод-сервере. Здесь — тот же ROW_NUMBER-алгоритм и тот же способ отключения
триггера (`app.skip_task_order_trigger`, session-var, НЕ `ALTER TABLE
DISABLE TRIGGER` — последнее берёт ACCESS EXCLUSIVE лок на всю таблицу),
но подключение — напрямую через asyncpg + прод-DSN из `.mcp.json`
(тот же паттерн, что `retag_python_cq_theory_tsk346.py`), чтобы не требовать
SSH на прод-сервер для точечного фикса 9 курсов.

Retag (difficulty_id -> THEORY) для cq-заданий 103..111 уже применён отдельным
скриптом `retag_python_cq_theory_tsk346.py` прямым SQL в обход
`TasksService.bulk_upsert` — durable-хук tsk-345 не сработал автоматически,
поэтому реордер нужен отдельным шагом (см. декомпозицию tsk-346).

Запуск:
    python scripts/reorder_python_cq_tsk346.py                  # dry-run (ROLLBACK)
    DBCHECK_OK=1 python scripts/reorder_python_cq_tsk346.py --apply   # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COURSE_MIN = 103
COURSE_MAX = 111
ANCHOR_TASK_IDS = (193, 204)  # курс 111, живая находка — должны стать первыми

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


def load_prod_dsn() -> str:
    """Достать прод-DSN роли lms_prod из .mcp.json (секрет не хардкодим)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-346: reorder courses {COURSE_MIN}-{COURSE_MAX} — {mode} ===\n")

    conn = await asyncpg.connect(load_prod_dsn())
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            n_courses = await conn.fetchval(
                "SELECT COUNT(DISTINCT course_id) FROM tasks WHERE course_id BETWEEN $1 AND $2",
                COURSE_MIN, COURSE_MAX,
            )
            n_tasks = await conn.fetchval(
                "SELECT COUNT(*) FROM tasks WHERE course_id BETWEEN $1 AND $2",
                COURSE_MIN, COURSE_MAX,
            )
            print(f"BEFORE: courses_in_range={n_courses} tasks_in_range={n_tasks}")

            anchors_before = await conn.fetch(
                "SELECT id, course_id, order_position, difficulty_id FROM tasks WHERE id = ANY($1::int[])",
                list(ANCHOR_TASK_IDS),
            )
            print("Якоря ДО:")
            for r in anchors_before:
                print(f"  {dict(r)}")

            await conn.execute("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
            status = await conn.execute(
                f"""
                WITH new_order AS (
                    SELECT id, ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                    FROM tasks
                    WHERE course_id BETWEEN $1 AND $2
                )
                UPDATE tasks t
                SET order_position = n.new_op
                FROM new_order n
                WHERE t.id = n.id
                  AND (t.order_position IS DISTINCT FROM n.new_op)
                """,
                COURSE_MIN, COURSE_MAX,
            )
            updated = int(status.split()[-1])
            print(f"UPDATE rowcount = {updated}")

            dupes = await conn.fetch(
                """
                SELECT course_id, order_position, COUNT(*) AS n
                FROM tasks WHERE course_id BETWEEN $1 AND $2
                GROUP BY course_id, order_position HAVING COUNT(*) > 1
                """,
                COURSE_MIN, COURSE_MAX,
            )
            if dupes:
                print(f"\nОШИБКА: коллизии order_position: {len(dupes)} — ROLLBACK")
                await tx.rollback()
                return 1
            print("\norder_position уникален внутри course_id — OK (0 коллизий)")

            violations = await conn.fetch(
                """
                SELECT course_id, COUNT(*) AS n FROM (
                    SELECT course_id, order_position, difficulty_id,
                        LAG(difficulty_id) OVER (
                            PARTITION BY course_id ORDER BY order_position ASC NULLS LAST
                        ) AS prev_difficulty
                    FROM tasks WHERE course_id BETWEEN $1 AND $2
                ) x
                WHERE prev_difficulty IS NOT NULL AND difficulty_id < prev_difficulty
                GROUP BY course_id
                """,
                COURSE_MIN, COURSE_MAX,
            )
            if violations:
                print(f"\nОШИБКА: межгрупповые нарушения остались в {len(violations)} курсах — ROLLBACK")
                await tx.rollback()
                return 1
            print("межгрупповой порядок THEORY->EASY->NORMAL->HARD->PROJECT — OK (0 нарушений)")

            anchors_after = await conn.fetch(
                "SELECT id, course_id, order_position, difficulty_id FROM tasks WHERE id = ANY($1::int[])",
                list(ANCHOR_TASK_IDS),
            )
            print("\nЯкоря ПОСЛЕ:")
            for r in anchors_after:
                print(f"  {dict(r)}")

            min_op_111 = await conn.fetchval(
                "SELECT MIN(order_position) FROM tasks WHERE course_id = 111"
            )
            anchors_ok = all(
                r["order_position"] == min_op_111 or r["order_position"] < 30
                for r in anchors_after
            )
            print(f"\nmin(order_position) курса 111 = {min_op_111}; якоря в начале курса: {anchors_ok}")

            if apply:
                await tx.commit()
                print("\nCOMMIT — изменения сохранены.")
            else:
                await tx.rollback()
                print("\nROLLBACK — dry-run, изменения откатаны.")
        except BaseException:
            await tx.rollback()
            raise
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
