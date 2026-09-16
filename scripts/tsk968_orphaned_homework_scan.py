# -*- coding: utf-8 -*-
"""tsk-968: масштаб находки — решённые задания, потерянные в стыке переиздания ДЗ.

Правило переиздания (`homework_service.issue`) каждый раз гасит предыдущую
выдачу и собирает НОВЫЙ состав через `_next_items` заново из дерева прогресса
на этот момент. Задание, которое ученик решил ПОСЛЕ отмены старой выдачи, но
которое не попало в состав ни одной последующей (потому что `_next_items`
корректно пропускает уже решённое — иначе завёл бы дубль), теряет счёт: оно
не входит в текущую активную выдачу (её "X из N" его не видит) и не входит
уже ни в одну другую — искать негде.

ТОЛЬКО ЧТЕНИЕ. Находит по всем ученикам: элемент, который был в составе
ОТМЕНЁННОЙ выдачи, решён ПОСЛЕ её отмены, и с тех пор ни разу не встретился
в составе ни одной более поздней выдачи того же ученика (включая текущую).

Запуск: `python scripts/tsk968_orphaned_homework_scan.py [--since ГГГГ-ММ-ДД]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#: Элемент отменённой выдачи, решённый после отмены и не подхваченный ни одной
#: более поздней выдачей того же ученика — та самая «потеря в стыке».
_ORPHANED_SQL = """
WITH item_done AS (
    SELECT hi.id AS item_id, hi.homework_id, hi.kind, hi.task_id, hi.material_id,
           ha.student_id, ha.cancelled_at, ha.due_at AS old_due_at,
           ha.id AS old_homework_id,
           CASE hi.kind
               WHEN 'task' THEN (
                   SELECT min(tr.submitted_at) FROM task_results tr
                     JOIN attempts a ON a.id = tr.attempt_id AND a.cancelled_at IS NULL
                    WHERE tr.user_id = ha.student_id AND tr.task_id = hi.task_id
                      AND tr.is_correct = true
               )
               ELSE (
                   SELECT coalesce(smp.completed_at, smp.skipped_at)
                     FROM student_material_progress smp
                    WHERE smp.student_id = ha.student_id AND smp.material_id = hi.material_id
                      AND smp.status IN ('completed', 'skipped')
               )
           END AS done_at
      FROM homework_item hi
      JOIN homework_assignment ha ON ha.id = hi.homework_id
     WHERE ha.cancelled_at IS NOT NULL
       AND ha.cancelled_at >= :since
)
SELECT id.student_id, u.full_name, id.old_homework_id, id.kind, id.task_id, id.material_id,
       id.cancelled_at, id.old_due_at, id.done_at,
       left(COALESCE(t.task_content->>'title', t.task_content->>'stem', m.title), 60) AS title
  FROM item_done id
  JOIN users u ON u.id = id.student_id
  LEFT JOIN tasks t ON t.id = id.task_id
  LEFT JOIN materials m ON m.id = id.material_id
 WHERE id.done_at IS NOT NULL
   AND id.done_at > id.cancelled_at
   AND NOT EXISTS (
        SELECT 1
          FROM homework_item hi2
          JOIN homework_assignment ha2 ON ha2.id = hi2.homework_id
         WHERE ha2.student_id = id.student_id
           AND ha2.issued_at >= id.cancelled_at
           AND ha2.id <> id.old_homework_id
           AND ( (id.kind = 'task' AND hi2.kind = 'task' AND hi2.task_id = id.task_id)
              OR (id.kind = 'material' AND hi2.kind = 'material' AND hi2.material_id = id.material_id) )
   )
 ORDER BY id.student_id, id.done_at
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


async def main(since: date) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)

    async with factory() as db:
        rows = (
            await db.execute(text(_ORPHANED_SQL), {"since": since_dt})
        ).mappings().all()

        by_student: dict[int, list] = {}
        for r in rows:
            by_student.setdefault(int(r["student_id"]), []).append(r)

        print(f"Отменённых выдач с {since:%d.%m.%Y} — найдено {len(rows)} потерянных решений "
              f"у {len(by_student)} учеников\n")
        for sid, items in by_student.items():
            print(f"{items[0]['full_name']} (#{sid}) — {len(items)}:")
            for r in items:
                kind_id = r["task_id"] or r["material_id"]
                print(
                    f"    {r['kind']:<9} id={kind_id:<6} \"{r['title']}\" "
                    f"— набор #{r['old_homework_id']} отменён {r['cancelled_at']:%d.%m %H:%M}, "
                    f"решено {r['done_at']:%d.%m %H:%M}"
                )
        print(f"\nИТОГО: {len(rows)} потерянных решений у {len(by_student)} учеников "
              f"(с {since:%d.%m.%Y}).")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=str, default="2026-09-01")
    args = parser.parse_args()
    since_date = date.fromisoformat(args.since)
    sys.exit(asyncio.run(main(since_date)))
