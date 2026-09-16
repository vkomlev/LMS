# -*- coding: utf-8 -*-
"""tsk-968: детальная цепочка выдач по конкретным ученикам. ТОЛЬКО ЧТЕНИЕ.

Нужно понять форму границы «цикла» переиздания: был ли due_at старого набора
ещё впереди в момент отмены (значит, это одна и та же логическая порция
работы, пересобранная раньше срока) или срок уже прошёл (значит, это новая
неделя/порция, и перенос долга был бы неверен).

Запуск: `python scripts/tsk968_chain_detail.py 4503 4504 4584`
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

_SQL = """
SELECT ha.id, ha.student_id, ha.issued_at, ha.due_at, ha.cancelled_at,
       ha.source, ha.occurrence_id, ha.planned_volume,
       (SELECT count(*) FROM homework_item hi WHERE hi.homework_id = ha.id) AS items
  FROM homework_assignment ha
 WHERE ha.student_id = ANY(:sids)
 ORDER BY ha.student_id, ha.issued_at
"""


def load_prod_dsn_asyncpg_style() -> str:
    mcp = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    raw = mcp["mcpServers"]["learn_prod_db"]["args"][-1]
    parts = urlsplit(raw)
    if "5.42.107.253" not in (parts.hostname or ""):
        raise RuntimeError(f"Ожидался прод-хост, получено: {parts.hostname}")
    return (
        f"postgresql+asyncpg://{parts.username}:{unquote(parts.password)}"
        f"@{parts.hostname}:{parts.port}{parts.path}"
    )


async def main(student_ids: list[int]) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        rows = (await db.execute(text(_SQL), {"sids": student_ids})).mappings().all()
        current_sid = None
        for r in rows:
            if r["student_id"] != current_sid:
                current_sid = r["student_id"]
                print(f"\n=== ученик {current_sid} ===")
            due_passed = "просрочен" if (r["cancelled_at"] and r["due_at"] and r["cancelled_at"] > r["due_at"]) else "ещё впереди"
            print(
                f"  #{r['id']:<5} issued={r['issued_at']:%d.%m %H:%M} "
                f"due={r['due_at']:%d.%m %H:%M} "
                f"cancelled={r['cancelled_at']:%d.%m %H:%M}" if r["cancelled_at"] else
                f"  #{r['id']:<5} issued={r['issued_at']:%d.%m %H:%M} due={r['due_at']:%d.%m %H:%M} (действует)"
            )
            if r["cancelled_at"]:
                print(
                    f"          due к моменту отмены: {due_passed}, "
                    f"source={r['source']}, occurrence={r['occurrence_id']}, items={r['items']}"
                )
            else:
                print(f"          source={r['source']}, occurrence={r['occurrence_id']}, items={r['items']}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]]
    sys.exit(asyncio.run(main(ids)))
