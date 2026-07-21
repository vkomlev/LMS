"""tsk-345 — разовый бэкфилл order_position для ЕГЭ-курсов (номерные «Задание N»,
courses 138-165), сломанных импортами ПОСЛЕ разового reorder-скрипта миграции
этапа 1.7 (2026-05-21, scripts/reorder_tasks_by_difficulty_type.py).

Отличие от исторического скрипта:
- Работает по явному диапазону course_id (по умолчанию 138-165), а не по ВСЕЙ
  таблице tasks — узкий blast radius для точечного фикса, не системной миграции.
- Тайбрейк внутри группы (difficulty_id + type) — ТЕКУЩИЙ order_position, а не
  id: сохраняет уже видимый учениками/методистами относительный порядок внутри
  сложности (в т.ч. ручной drag-and-drop реордер), меняет только межгрупповые
  границы THEORY→EASY→NORMAL→HARD→PROJECT.
- Та же ROW_NUMBER-логика теперь живёт постоянно в
  app/services/tasks_service.py::TasksService._reorder_tasks_by_difficulty
  (durable-фикс, вызывается автоматически из bulk_upsert) — этот скрипт нужен
  только чтобы разово починить уже накопленный на проде разъезд.
- Отключает trg_set_task_order_position через session-variable
  app.skip_task_order_trigger (SELECT set_config(..., true) — is_local),
  НЕ через ALTER TABLE ... DISABLE TRIGGER: последнее берёт ACCESS EXCLUSIVE
  лок на ВСЮ таблицу tasks (блокирует live-запросы студентов по всем курсам,
  не только по перечисленным). Тот же паттерн, что TasksRepository.reorder_tasks.

Запуск (на прод-сервере, .env с прод DSN):
    python scripts/reorder_courses_by_difficulty_tsk345.py                    # dry-run
    python scripts/reorder_courses_by_difficulty_tsk345.py --apply            # COMMIT
    python scripts/reorder_courses_by_difficulty_tsk345.py --course-min 138 --course-max 165
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


async def main(apply: bool, course_min: int, course_max: int) -> int:
    mode = "APPLY (COMMIT)" if apply else "DRY-RUN (ROLLBACK)"
    print(f"=== tsk-345: reorder courses {course_min}-{course_max} — {mode} ===\n")

    async with async_session_factory() as db:
        n_courses = (await db.execute(
            text(
                "SELECT COUNT(DISTINCT course_id) FROM tasks "
                "WHERE course_id BETWEEN :lo AND :hi"
            ),
            {"lo": course_min, "hi": course_max},
        )).scalar()
        n_tasks = (await db.execute(
            text("SELECT COUNT(*) FROM tasks WHERE course_id BETWEEN :lo AND :hi"),
            {"lo": course_min, "hi": course_max},
        )).scalar()
        print(f"BEFORE: courses_in_range={n_courses} tasks_in_range={n_tasks}")

        # Контрольная задача из живой находки (2026-07-21): должна уехать
        # после EASY/NORMAL своего курса.
        before_2059 = (await db.execute(text(
            "SELECT course_id, order_position, difficulty_id FROM tasks WHERE id = 2059"
        ))).mappings().first()
        if before_2059:
            print(f"BEFORE id=2059: {dict(before_2059)}")

        await db.execute(
            text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
        )
        result = await db.execute(text(f"""
            WITH new_order AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER ({ORDER_BY_EXPR.strip()}) AS new_op
                FROM tasks
                WHERE course_id BETWEEN :lo AND :hi
            )
            UPDATE tasks t
            SET order_position = n.new_op
            FROM new_order n
            WHERE t.id = n.id
              AND (t.order_position IS DISTINCT FROM n.new_op)
        """), {"lo": course_min, "hi": course_max})
        print(f"UPDATE rowcount = {result.rowcount}")

        # Верификация: уникальность order_position внутри course_id (координация с tsk-337).
        dupes = (await db.execute(text("""
            SELECT course_id, order_position, COUNT(*) AS n
            FROM tasks
            WHERE course_id BETWEEN :lo AND :hi
            GROUP BY course_id, order_position
            HAVING COUNT(*) > 1
        """), {"lo": course_min, "hi": course_max})).fetchall()
        if dupes:
            print(f"\nОШИБКА: коллизии order_position после реордера: {len(dupes)} — ROLLBACK")
            await db.rollback()
            return 1
        print("\norder_position уникален внутри course_id — OK (0 коллизий)")

        # Верификация: межгрупповой порядок THEORY..PROJECT не нарушен.
        violations = (await db.execute(text("""
            SELECT course_id, COUNT(*) AS n FROM (
                SELECT course_id, order_position, difficulty_id,
                    LAG(difficulty_id) OVER (
                        PARTITION BY course_id ORDER BY order_position ASC NULLS LAST
                    ) AS prev_difficulty
                FROM tasks WHERE course_id BETWEEN :lo AND :hi
            ) x
            WHERE prev_difficulty IS NOT NULL AND difficulty_id < prev_difficulty
            GROUP BY course_id
        """), {"lo": course_min, "hi": course_max})).fetchall()
        if violations:
            print(f"\nОШИБКА: межгрупповые нарушения остались в {len(violations)} курсах — ROLLBACK")
            await db.rollback()
            return 1
        print("межгрупповой порядок THEORY->EASY->NORMAL->HARD->PROJECT — OK (0 нарушений)")

        after_2059 = (await db.execute(text(
            "SELECT course_id, order_position, difficulty_id FROM tasks WHERE id = 2059"
        ))).mappings().first()
        if after_2059:
            print(f"\nAFTER id=2059: {dict(after_2059)}")

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
    ap.add_argument("--course-min", type=int, default=138)
    ap.add_argument("--course-max", type=int, default=165)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(
        apply=args.apply, course_min=args.course_min, course_max=args.course_max,
    )))
