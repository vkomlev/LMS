# -*- coding: utf-8 -*-
"""tsk-968: живая проверка на проде — карточка Крук Анастасии (4503) с фиксом.

Зовёт НАСТОЯЩИЙ `homework_service.get_current` на боевых данных. Только
чтение — фикс не требует записи: total/done считаются заново при каждом
чтении, ничего не хранится и не нужно пересчитывать задним числом.

Запуск: `python scripts/tsk968_verify_kruk.py`
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

STUDENT_ID = 4503
EXPECTED_DONE_TASK_IDS = {10365, 10366, 268}


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


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services import homework_service

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        current = await homework_service.get_current(db, student_id=STUDENT_ID)
        print(f"Домашняя работа: {current['done']} из {current['total']}")
        found_ids = {i["item_id"] for i in current["items"] if i["done"]}
        for item in current["items"]:
            mark = "V" if item["done"] else " "
            print(f"  [{mark}] {item['kind']:<9} id={item['item_id']:<6} \"{item['title']}\"")

        missing = EXPECTED_DONE_TASK_IDS - found_ids
        if missing:
            print(f"\nОШИБКА: не найдены как выполненные: {sorted(missing)}")
            await engine.dispose()
            return 1
        print(f"\nOK: все три задания {sorted(EXPECTED_DONE_TASK_IDS)} видны выполненными.")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
