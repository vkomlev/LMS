# -*- coding: utf-8 -*-
"""tsk-350: выгрузка заданий с прода для автосверки дублей (read-only).

Воссоздан в tsk-390: исходный дампер не сохранился в репозитории, а `tasks_dump.json`
не коммитится (крупный). Формат — ровно тот, что ждёт `detect.load()`: JSON-массив строк
с полями id, course_id, course_uid, external_uid, is_active и тремя jsonb-полями.

Зачем перевыгружать: имя файла-вложения в LMS — это SHA256 его содержимого, поэтому
доскачанные файлы (tsk-390) дают новые совпадения по sha => всплывают дубли, которые
раньше различались лишь наличием файла.

Только SELECT. Прод-DSN берётся из .mcp.json (значение не печатается).

Запуск:  python reviews/tsk350-dedup/dump_tasks.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
from tsk369_collect import dsn  # noqa: E402

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

SQL = """
SELECT t.id, t.course_id, c.course_uid, t.external_uid, t.is_active,
       t.task_content::text            AS task_content,
       t.solution_rules::text          AS solution_rules,
       t.difficulty_provenance::text   AS difficulty_provenance
FROM tasks t
JOIN courses c ON c.id = t.course_id
ORDER BY t.id
"""


async def main() -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(SQL)
    finally:
        await conn.close()

    data = [dict(r) for r in rows]
    out = HERE / "tasks_dump.json"
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    active = sum(1 for r in data if r["is_active"])
    print(f"Выгружено заданий: {len(data)} (активных {active})")
    print(f"Сохранено: {out}  ({out.stat().st_size/1e6:.1f} МБ)")


if __name__ == "__main__":
    asyncio.run(main())
