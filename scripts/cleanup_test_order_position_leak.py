"""Одноразовая чистка артефактов test_tasks_order_position_api.py.

`TasksService.bulk_upsert` через BaseRepository делает COMMIT внутри
(см. `app/repos/base.py:70`), поэтому тесты, использовавшие сервис, оставили
строки в `tasks` и `courses` с title='test_op_api' / 'test_order_position'.

Отбор по названию — признак, а не доказательство авторства (tsk-885), поэтому
скрипт сначала показывает найденное и пишет только по `--apply`. На боевом
подключении отказывает: там курс с таким названием означал бы чужую работу,
а не след теста.

Запуск:
    python scripts/cleanup_test_order_position_leak.py            # предпросмотр
    python scripts/cleanup_test_order_position_leak.py --apply    # запись
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from app.core.db_targets import assert_not_prod  # noqa: E402
from app.db.session import async_session_factory  # type: ignore  # noqa: E402

_TITLES = ("test_op_api", "test_order_position")


async def main(apply: bool) -> None:
    assert_not_prod(what="чистка следов теста order_position")
    async with async_session_factory() as session:
        found = (
            await session.execute(
                text(
                    "SELECT c.id, c.title, "
                    "  (SELECT count(*) FROM tasks t WHERE t.course_id = c.id) AS tasks "
                    "FROM courses c WHERE c.title = ANY(:titles) ORDER BY c.id"
                ),
                {"titles": list(_TITLES)},
            )
        ).all()
        print(f"Курсов с тестовыми названиями: {len(found)}")
        for row in found:
            print(f"  #{row[0]}  {row[1]}  заданий {row[2]}")
        if not apply:
            print("\nЭто предпросмотр. Записать: добавить --apply")
            return

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

        left = (
            await session.execute(
                text("SELECT count(*) FROM courses WHERE title = ANY(:titles)"),
                {"titles": list(_TITLES)},
            )
        ).scalar()
        print(f"Сверка: осталось курсов {left}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="записать изменения")
    asyncio.run(main(parser.parse_args().apply))
