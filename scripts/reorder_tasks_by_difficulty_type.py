"""tsk-004 Этап 1.7 — переупорядочить tasks.order_position.

Правило сортировки внутри каждого course_id:
  1. difficulty_id ASC (1=Теория, 2=Легко, 3=Средняя, 4=Сложно, 5=Проект)
  2. group_type ASC, где:
       1 = SC, MC      (выбор из вариантов — самые простые)
       2 = TA, SA      (текстовый/короткий ответ)
       3 = SA_COM      (короткий ответ + проверка кода)
  3. id ASC (стабильный tiebreaker)

ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY <rule>) → order_position.

Операция в одной транзакции с временным DISABLE триггеров:
  - trg_set_task_order_position (BEFORE INSERT/UPDATE)
  - trg_reorder_tasks_after_delete (AFTER DELETE)
(Чтобы массовый UPDATE не вызывал per-row реорганизацию.)

Запуск:
    python scripts/reorder_tasks_by_difficulty_type.py            # dry-run
    python scripts/reorder_tasks_by_difficulty_type.py --apply    # COMMIT
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env", encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # noqa: E402

# Новый порядок задаётся выражением ниже. Изменения порядка по типам —
# править только эту CASE-конструкцию.
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
        id ASC
"""


async def main(apply: bool) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== Reorder tasks by difficulty/type — {mode} ===\n")

    async with async_session_factory() as db:
        # BEFORE: разрезы для отчёта
        n_total = (await db.execute(text("SELECT COUNT(*) FROM tasks"))).scalar()
        n_courses = (await db.execute(
            text("SELECT COUNT(DISTINCT course_id) FROM tasks WHERE course_id IS NOT NULL")
        )).scalar()
        n_no_op = (await db.execute(
            text("SELECT COUNT(*) FROM tasks WHERE order_position IS NULL")
        )).scalar()
        print(f"BEFORE: tasks_total={n_total} courses_with_tasks={n_courses} "
              f"order_position_null={n_no_op}")

        # DISABLE triggers
        for trg in ("trg_set_task_order_position", "trg_reorder_tasks_after_delete"):
            await db.execute(text(f"ALTER TABLE tasks DISABLE TRIGGER {trg}"))
        try:
            # Массовый UPDATE — все 568 задач сразу
            result = await db.execute(text(f"""
                WITH new_order AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                    FROM tasks
                    WHERE course_id IS NOT NULL
                )
                UPDATE tasks t
                SET order_position = n.new_op
                FROM new_order n
                WHERE t.id = n.id
                  AND (t.order_position IS DISTINCT FROM n.new_op)
            """))
            print(f"UPDATE rowcount = {result.rowcount}")
        finally:
            for trg in ("trg_set_task_order_position", "trg_reorder_tasks_after_delete"):
                await db.execute(text(f"ALTER TABLE tasks ENABLE TRIGGER {trg}"))

        # AFTER: верификация — каждый курс должен иметь сплошной 1..N
        gaps = (await db.execute(text("""
            WITH q AS (
                SELECT course_id, order_position,
                       ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY order_position) AS rn
                FROM tasks WHERE course_id IS NOT NULL
            )
            SELECT course_id, COUNT(*) AS mismatch
            FROM q WHERE order_position <> rn
            GROUP BY course_id
        """))).fetchall()
        if gaps:
            print(f"\nВНИМАНИЕ: курсов с разрывами order_position: {len(gaps)}")
            for g in gaps[:5]:
                print(f"  course_id={g.course_id} mismatch={g.mismatch}")
        else:
            print("\norder_position сплошной 1..N во всех курсах OK")

        # Сэмпл: первые задачи в 3 курсах
        sample_courses = (await db.execute(text("""
            SELECT DISTINCT course_id FROM tasks WHERE course_id IS NOT NULL
            ORDER BY course_id LIMIT 3
        """))).fetchall()
        print("\n--- Сэмпл порядка (первые 6 задач в курсах) ---")
        for c in sample_courses:
            cid = c.course_id
            rows = (await db.execute(text("""
                SELECT id, order_position, difficulty_id, task_content->>'type' AS t
                FROM tasks WHERE course_id = :c
                ORDER BY order_position LIMIT 6
            """), {"c": cid})).fetchall()
            print(f"course_id={cid}:")
            for r in rows:
                print(f"  op={r.order_position:>3}  id={r.id:>3}  "
                      f"difficulty={r.difficulty_id}  type={r.t}")

        if apply:
            await db.commit()
            print("\nCOMMIT — изменения сохранены.")
        else:
            await db.rollback()
            print("\nROLLBACK — dry-run, изменения откатаны.")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Выполнить COMMIT.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
