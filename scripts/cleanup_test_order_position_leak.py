"""Одноразовая чистка артефактов test_tasks_order_position_api.py.

`TasksService.bulk_upsert` через BaseRepository делает COMMIT внутри
(см. `app/repos/base.py:70`), поэтому тесты, использовавшие сервис, оставили
строки в `tasks` и `courses` с title='test_op_api' / 'test_order_position'.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_factory  # type: ignore  # noqa: E402


async def main() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            res = await session.execute(
                text(
                    """
                    WITH del_tasks AS (
                        DELETE FROM tasks WHERE course_id IN (
                            SELECT id FROM courses
                            WHERE title IN ('test_op_api', 'test_order_position')
                        )
                        RETURNING id
                    )
                    SELECT COUNT(*) AS n FROM del_tasks
                    """
                )
            )
            tasks_deleted = res.scalar() or 0

            res = await session.execute(
                text(
                    """
                    WITH del_courses AS (
                        DELETE FROM courses
                        WHERE title IN ('test_op_api', 'test_order_position')
                        RETURNING id
                    )
                    SELECT COUNT(*) AS n FROM del_courses
                    """
                )
            )
            courses_deleted = res.scalar() or 0

        print(f"cleanup done: tasks={tasks_deleted}, courses={courses_deleted}")


if __name__ == "__main__":
    asyncio.run(main())
