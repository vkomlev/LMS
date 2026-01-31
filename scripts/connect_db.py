# Проверка подключения к БД (когда MCP недоступен).
# Запуск из корня проекта: python scripts/connect_db.py
# Перед smoke-тестами материалов можно взять реальные course_id из вывода.

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


async def main():
    from sqlalchemy import text
    from app.db.session import async_session_factory

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            row = (
                await session.execute(
                    text(
                        "SELECT id, course_uid FROM courses WHERE course_uid IN ('COURSE-PY-01', 'COURSE-MATH-01') ORDER BY id"
                    )
                )
            ).fetchall()
        print("OK: DB connection successful.")
        if row:
            print("Courses for smoke tests:", [{"id": r[0], "course_uid": r[1]} for r in row])
        return 0
    except Exception as e:
        print("FAIL: DB connection error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
