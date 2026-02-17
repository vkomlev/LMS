"""
Заполнение таблицы difficulties базовыми уровнями сложности.

Запуск из корня проекта:
  python scripts/seed_difficulties.py

Идемпотентно: можно запускать многократно.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(dotenv_path=project_root / ".env", encoding="utf-8-sig")


DIFFICULTIES = [
    ("THEORY", "Теория", 1),
    ("EASY", "Легко", 2),
    ("NORMAL", "Средняя", 3),
    ("HARD", "Сложно", 4),
    ("PROJECT", "Проект", 5),
]


async def main() -> int:
    from sqlalchemy import text
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO difficulties (code, name_ru, weight)
                VALUES (:code, :name_ru, :weight)
                ON CONFLICT (code) DO UPDATE
                SET name_ru = EXCLUDED.name_ru,
                    weight = EXCLUDED.weight
                """
            ),
            [{"code": c, "name_ru": n, "weight": w} for c, n, w in DIFFICULTIES],
        )
        await session.commit()

        result = await session.execute(
            text("SELECT id, code, name_ru, weight FROM difficulties ORDER BY weight, id")
        )
        rows = result.fetchall()

    print("OK: difficulties seeded/updated.")
    for r in rows:
        print(f"- id={r[0]} code={r[1]} name_ru={r[2]} weight={r[3]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)

