# -*- coding: utf-8 -*-
"""tsk-690: вывести подкурс 165 «Черепашья графика с модулем Turtle» из зачёта.

ПОЧЕМУ ИМЕННО requirement_level, А НЕ `courses.is_required`
`courses.is_required` движок не читает вовсе: он живёт только в импорте курсов из
Google Sheets (`courses_sheets_parser_service`). Зачёт и «долг» считаются по
`requirement_level IN ('required','skippable')` содержимого — так в
`me_service` (счётчики кабинета) и в `learning_engine_service` (следующий шаг,
состояние курса). Значит `recommended` — единственный уровень, выводящий пункт
из зачёта, и менять надо содержимое узла, а не флаг курса.

ЧТО ИМЕННО ПРАВИМ (проверено read-only на проде 2026-08-26)
Курс 165 висит в 157 («Задание 6 ЕГЭ. Исполнитель Черепаха») на позиции 6.
- 10 заданий `required` (id 10029-10038) — решены только у 4512, 4520, 4526;
  у пятерых (4500, 4506, 4507, 4511, 4519) это и есть долг;
- 8 заданий уже `recommended` — не трогаем;
- 4 материала `required` (310, 311, 312, 314) — закрыты у семерых из восьми.
Само задание 6 (курс 157) зачтено: 32 обязательных задания верно у семерых,
у 4519 — 27 из 32 (он ещё идёт); материалы курса 157 закрыты у всех восьмерых.
Пересчитано по `task_results.is_correct`: таблица `student_task_progress`
пуста целиком (0 строк во всей базе) и источником прогресса быть не может.

БЕЗОПАСНОСТЬ ЗАПИСИ
`order_position` не трогаем — триггеры `trg_set_*_order_position` при UPDATE
выходят сразу (`NEW.order_position = old_order`), перенумерации не будет.
`trg_task_audit_update` пишет запись аудита только при смене course_id /
is_active / solution_rules — их не касаемся.

ОБРАТИМОСТЬ
UPDATE обратно в 'required' по тем же id возвращает как было; удалений нет.

Запуск: dry-run по умолчанию; `--apply` — запись (нужен DBCHECK_OK=1).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

COURSE_ID = 165
TASK_IDS = [10029, 10030, 10031, 10032, 10033, 10034, 10035, 10036, 10037, 10038]
MATERIAL_IDS = [310, 311, 312, 314]


def _dsn() -> str:
    """Прод-DSN learn: из окружения, иначе из `.mcp.json` (секрет не печатаем)."""
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers["learn_prod_db"]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                dsn = arg
                break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError(
            "Не нашёл прод-DSN learn (5.42.107.253/learn). Передай LEARN_PROD_DSN явно."
        )
    return dsn


async def _snapshot(conn: asyncpg.Connection) -> None:
    """Показать текущие уровни содержимого курса и состояние учеников."""
    tasks = await conn.fetch(
        """
        SELECT requirement_level, count(*) AS n
        FROM tasks WHERE course_id = $1 AND is_active
        GROUP BY requirement_level ORDER BY requirement_level
        """,
        COURSE_ID,
    )
    mats = await conn.fetch(
        """
        SELECT requirement_level, count(*) AS n
        FROM materials WHERE course_id = $1 AND is_active
        GROUP BY requirement_level ORDER BY requirement_level
        """,
        COURSE_ID,
    )
    print(f"Курс {COURSE_ID}, активное содержимое:")
    print("  задания:   " + ", ".join(f"{r['requirement_level']}={r['n']}" for r in tasks))
    print("  материалы: " + ", ".join(f"{r['requirement_level']}={r['n']}" for r in mats))

    debtors = await conn.fetch(
        """
        WITH req_t AS (
            SELECT id FROM tasks
            WHERE course_id = $1 AND is_active AND requirement_level IN ('required','skippable')
        ),
        req_m AS (
            SELECT id FROM materials
            WHERE course_id = $1 AND is_active AND requirement_level IN ('required','skippable')
        ),
        st AS (SELECT user_id FROM user_courses WHERE course_id = 112 AND is_active)
        SELECT st.user_id, u.full_name,
               (SELECT count(*) FROM req_t) - (
                   SELECT count(DISTINCT tr.task_id) FROM task_results tr
                   WHERE tr.user_id = st.user_id AND tr.is_correct
                     AND tr.task_id IN (SELECT id FROM req_t)
               ) AS tasks_left,
               (SELECT count(*) FROM req_m) - (
                   SELECT count(*) FROM student_material_progress s
                   WHERE s.student_id = st.user_id AND s.status = 'completed'
                     AND s.material_id IN (SELECT id FROM req_m)
               ) AS materials_left
        FROM st JOIN users u ON u.id = st.user_id
        WHERE EXISTS (
            SELECT 1 FROM student_material_progress s
            WHERE s.student_id = st.user_id
              AND s.material_id IN (SELECT id FROM materials WHERE course_id = 157)
        )
        ORDER BY st.user_id
        """,
        COURSE_ID,
    )
    print("Ученики, дошедшие до задания 6 (курс 157) — сколько пунктов Turtle им зачтено в долг:")
    for r in debtors:
        print(
            f"  {r['user_id']} {r['full_name']}: "
            f"заданий в долге {r['tasks_left']}, материалов в долге {r['materials_left']}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-690: Turtle (курс 165) → рекомендуемый")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    parser.add_argument(
        "--tasks-only",
        action="store_true",
        help="перевести только задания, материалы оставить обязательными",
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        print("=== ДО ===")
        await _snapshot(conn)

        target_tasks = await conn.fetch(
            "SELECT id, order_position, requirement_level FROM tasks "
            "WHERE id = ANY($1::int[]) AND course_id = $2 ORDER BY order_position",
            TASK_IDS,
            COURSE_ID,
        )
        target_mats = await conn.fetch(
            "SELECT id, order_position, requirement_level FROM materials "
            "WHERE id = ANY($1::int[]) AND course_id = $2 ORDER BY order_position",
            MATERIAL_IDS,
            COURSE_ID,
        )
        # Страховка: правим ровно то, что нашла разведка, и только внутри курса 165.
        if len(target_tasks) != len(TASK_IDS):
            print("СТОП: не все задания из списка нашлись в курсе 165 — состав изменился.")
            return 2
        if not args.tasks_only and len(target_mats) != len(MATERIAL_IDS):
            print("СТОП: не все материалы из списка нашлись в курсе 165 — состав изменился.")
            return 2

        print(f"\nПлан: {len(target_tasks)} заданий → recommended", end="")
        print("" if args.tasks_only else f", {len(target_mats)} материалов → recommended")

        if not args.apply:
            print("\nDry-run: записи не было.")
            return 0

        async with conn.transaction():
            st = await conn.execute(
                "UPDATE tasks SET requirement_level = 'recommended' "
                "WHERE id = ANY($1::int[]) AND course_id = $2 AND requirement_level <> 'recommended'",
                TASK_IDS,
                COURSE_ID,
            )
            tasks_updated = int(st.rsplit(" ", 1)[-1] or 0)
            mats_updated = 0
            if not args.tasks_only:
                st = await conn.execute(
                    "UPDATE materials SET requirement_level = 'recommended' "
                    "WHERE id = ANY($1::int[]) AND course_id = $2 "
                    "AND requirement_level <> 'recommended'",
                    MATERIAL_IDS,
                    COURSE_ID,
                )
                mats_updated = int(st.rsplit(" ", 1)[-1] or 0)
            print(f"\nОбновлено: заданий {tasks_updated}, материалов {mats_updated}")

        print("\n=== ПОСЛЕ ===")
        await _snapshot(conn)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
