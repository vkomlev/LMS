"""
Вывести базовые счётчики БД (для smoke-тестов).

Запуск из корня проекта:
  python scripts/db_counts.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


async def main() -> int:
    from sqlalchemy import text
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        res = await session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM courses)       AS courses_count,
                  (SELECT count(*) FROM difficulties)  AS difficulties_count,
                  (SELECT count(*) FROM tasks)         AS tasks_count,
                  (SELECT count(*) FROM attempts)      AS attempts_count,
                  (SELECT count(*) FROM task_results)  AS task_results_count
                """
            )
        )
        row = res.first()

    print(
        {
            "courses": int(row[0]),
            "difficulties": int(row[1]),
            "tasks": int(row[2]),
            "attempts": int(row[3]),
            "task_results": int(row[4]),
        }
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)

