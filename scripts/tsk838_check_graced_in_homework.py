# -*- coding: utf-8 -*-
"""tsk-838: не задано ли ученикам прощённое содержимое. ТОЛЬКО ЧТЕНИЕ.

Правило tsk-692: то, что добавили в курс ПОСЛЕ прохождения темы, для ученика
необязательно — движок такое не предлагает и не считает в прогрессе. Выдача
домашней работы про правило не знала и брала такие элементы в состав: ученица
прошла «Первую программу» 21 июля, 7 сентября в курс досыпали два задания, и
8 сентября они пришли ей домой как долг.

Скрипт зовёт НАСТОЯЩИЙ `compute_graced_items` на боевых данных и показывает,
в чьих действующих выдачах такие элементы есть. Ничего не пишет.

Запуск: `python scripts/tsk838_check_graced_in_homework.py`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#: Действующие выдачи с составом.
_SQL = """
SELECT ha.id, ha.student_id, u.full_name,
       hi.kind, hi.task_id, hi.material_id,
       COALESCE(t.course_id, m.course_id) AS course_id,
       left(COALESCE(t.task_content->>'title', m.title), 46) AS title
  FROM homework_assignment ha
  JOIN users u ON u.id = ha.student_id
  JOIN homework_item hi ON hi.homework_id = ha.id
  LEFT JOIN tasks t ON t.id = hi.task_id
  LEFT JOIN materials m ON m.id = hi.material_id
 WHERE ha.cancelled_at IS NULL AND ha.due_at > now()
 ORDER BY ha.student_id, hi.position
"""

#: Корневые курсы ученика — от них считается прощение (правило смотрит предков).
_ROOTS_SQL = """
SELECT course_id FROM user_courses
 WHERE user_id = :sid AND is_active = true
"""


def load_prod_dsn_asyncpg_style() -> str:
    """DSN прод-роли из `.mcp.json` в формате SQLAlchemy (секрет не печатаем)."""
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services.content_grace_service import compute_graced_items

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        rows = (await db.execute(text(_SQL))).mappings().all()
        by_student: dict[int, list] = {}
        for r in rows:
            by_student.setdefault(int(r["student_id"]), []).append(r)

        print(f"Действующих выдач: {len(by_student)} учеников\n")
        affected = 0
        total_items = 0
        for student_id, items in by_student.items():
            roots = (
                await db.execute(text(_ROOTS_SQL), {"sid": student_id})
            ).scalars().all()
            graced_tasks: set[int] = set()
            graced_materials: set[int] = set()
            for root in roots:
                graced = await compute_graced_items(db, student_id, int(root))
                graced_tasks |= set(graced.tasks)
                graced_materials |= set(graced.materials)

            bad = [
                r for r in items
                if (r["kind"] == "task" and int(r["task_id"] or 0) in graced_tasks)
                or (
                    r["kind"] == "material"
                    and int(r["material_id"] or 0) in graced_materials
                )
            ]
            if not bad:
                continue
            affected += 1
            total_items += len(bad)
            print(f"{items[0]['full_name']} (#{student_id}) — {len(bad)} из {len(items)}:")
            for r in bad:
                print(f"    {r['kind']:<9} курс {r['course_id']:<5} {r['title']}")

        print(
            f"\nИТОГ: у {affected} учеников в действующей выдаче "
            f"{total_items} прощённых элементов."
        )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
