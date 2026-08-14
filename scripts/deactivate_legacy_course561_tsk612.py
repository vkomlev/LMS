# -*- coding: utf-8 -*-
"""tsk-612 (хвост): вывести из оборота курс 561 «Архив: контент Виктора Комлева (legacy)».

ПОЧЕМУ ТАК, А НЕ «ПОСТАВИТЬ СТАТУС КУРСУ»
У таблицы `courses` нет ни `is_active`, ни `status`, ни `archived_at` — архивации
курсов в схеме LMS не существует вовсе (проверено по information_schema
2026-08-14). Единственный способ вывести курс из активного оборота — выключить
его задания (`tasks.is_active = false`): именно этот флаг гейтит выдачу заданий
ученику и попадание в активные выборки.

BLAST RADIUS — НУЛЕВОЙ (проверено read-only на проде)
Курс 561: 193 задания (все активны), 0 попыток учеников (`task_results`),
0 зачислений (`user_courses`), 0 материалов, 0 строк `student_course_state`,
и он не связан в графе (`course_parents`) ни как родитель, ни как ребёнок.
Дойти до него ученик не может. Названия заданий (tsk-612) остаются на месте.

ОБРАТИМОСТЬ
`UPDATE tasks SET is_active = true WHERE course_id = 561` возвращает как было.

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

COURSE_ID = 561


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


async def main() -> int:
    parser = argparse.ArgumentParser(description="tsk-612: вывести курс 561 из оборота")
    parser.add_argument("--apply", action="store_true", help="записать в прод-БД")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT c.title,
                   count(*) FILTER (WHERE t.is_active) AS active,
                   count(*) AS total,
                   (SELECT count(*) FROM task_results tr
                     JOIN tasks t2 ON t2.id = tr.task_id WHERE t2.course_id = $1) AS results,
                   (SELECT count(*) FROM user_courses WHERE course_id = $1) AS enrolled
            FROM courses c JOIN tasks t ON t.course_id = c.id
            WHERE c.id = $1
            GROUP BY c.title
            """,
            COURSE_ID,
        )
        if row is None:
            print(f"Курс {COURSE_ID} не найден или у него нет заданий — нечего делать.")
            return 1

        print(f"Курс {COURSE_ID}: «{row['title']}»")
        print(f"  заданий всего: {row['total']}, активных: {row['active']}")
        print(f"  попыток учеников: {row['results']}, зачислений: {row['enrolled']}")

        # Страховка: выключаем только курс БЕЗ следов учебной жизни. Если у
        # курса появились попытки или зачисления — это уже не архив, и решение
        # принимает человек, а не скрипт.
        if row["results"] or row["enrolled"]:
            print("СТОП: у курса есть попытки или зачисления — выключать вслепую нельзя.")
            return 2

        if not args.apply:
            print(f"\nDry-run: выключил бы {row['active']} заданий. Записи не было.")
            return 0

        async with conn.transaction():
            status = await conn.execute(
                "UPDATE tasks SET is_active = false WHERE course_id = $1 AND is_active IS TRUE",
                COURSE_ID,
            )
        updated = int(status.rsplit(" ", 1)[-1] or 0)
        left = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE course_id = $1 AND is_active IS TRUE", COURSE_ID
        )
        print(f"Выключено заданий: {updated}. Активных в курсе осталось: {left}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
