"""Одноразовый дамп `(id, course_id)` всех tasks в CSV для T25 snapshot-теста.

Используется до миграции `tasks_order_position_triggers` для фиксации baseline,
по которому будет проверена эквивалентность сортировки до/после.
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

# Подключаемся как обычные скрипты LMS
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # type: ignore  # noqa: E402


async def main() -> None:
    out_path = ROOT / "reviews" / "2026-05-21-tasks-order-position-baseline.csv"
    out_path.parent.mkdir(exist_ok=True)

    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT id, course_id FROM public.tasks ORDER BY course_id ASC, id ASC")
        )
        rows = result.all()

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "course_id"])
        for row in rows:
            writer.writerow([row.id, row.course_id])

    print(f"baseline saved: {out_path} ({len(rows)} rows)")


if __name__ == "__main__":
    asyncio.run(main())
