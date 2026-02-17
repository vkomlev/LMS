"""
Короткий отчёт по импортированным задачам (для smoke-тестов).

Показывает:
- общее количество задач
- распределение по курсам (course_uid) и сложности (difficulty.code)

Запуск:
  python scripts/tasks_import_report.py
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
        total = (await session.execute(text("select count(*) from tasks"))).scalar_one()
        print({"tasks_total": int(total)})

        rows = (
            await session.execute(
                text(
                    """
                    select
                      c.course_uid as course_uid,
                      d.code as difficulty_code,
                      count(*) as tasks_count
                    from tasks t
                    join courses c on c.id = t.course_id
                    join difficulties d on d.id = t.difficulty_id
                    group by c.course_uid, d.code
                    order by tasks_count desc, c.course_uid asc
                    """
                )
            )
        ).all()

        for r in rows:
            print(f"- course_uid={r[0]} difficulty={r[1]} count={int(r[2])}")

        samples = (
            await session.execute(
                text(
                    """
                    select t.external_uid, c.course_uid, d.code
                    from tasks t
                    join courses c on c.id = t.course_id
                    join difficulties d on d.id = t.difficulty_id
                    order by t.id
                    limit 10
                    """
                )
            )
        ).all()
        print("samples:")
        for s in samples:
            print(f"  - external_uid={s[0]} course_uid={s[1]} difficulty={s[2]}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print("FAIL:", e, file=sys.stderr)
        sys.exit(1)

