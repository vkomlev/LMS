# -*- coding: utf-8 -*-
"""tsk-741: проверка сдвоенного часа на боевых данных. ТОЛЬКО ЧТЕНИЕ.

Вопрос оператора 02.09: у ученика два занятия подряд — не назначится ли после
первого задание, которое выполнить невозможно? Назначалось: срок брался по
следующему занятию, а у сдвоенного часа следующее начинается через перемену.

Скрипт зовёт НАСТОЯЩИЙ код (`homework_service.next_due_for`) на настоящих
данных и показывает, куда он ставит срок для реальных сдвоенных пар. Ничего не
пишет: ни выдач, ни отметок — только SELECT.

Запуск: `python scripts/tsk741_check_paired_lessons.py`
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Сколько сдвоенных пар показать.
LIMIT = 6

_PAIRS_SQL = """
WITH chain AS (
    SELECT lop.student_id, lo.id, lo.scheduled_at, lo.duration_minutes,
           lead(lo.scheduled_at) OVER (
               PARTITION BY lop.student_id ORDER BY lo.scheduled_at
           ) AS next_at,
           lead(lo.id) OVER (
               PARTITION BY lop.student_id ORDER BY lo.scheduled_at
           ) AS next_id
      FROM lesson_occurrence_participant lop
      JOIN lesson_occurrence lo ON lo.id = lop.occurrence_id
     WHERE lop.status <> 'rescheduled'
)
SELECT c.student_id, u.full_name, c.id AS first_id, c.scheduled_at AS first_at,
       c.duration_minutes, c.next_id, c.next_at
  FROM chain c
  JOIN users u ON u.id = c.student_id
 WHERE c.next_at IS NOT NULL
   AND c.next_at - (c.scheduled_at + (c.duration_minutes || ' minutes')::interval)
       BETWEEN interval '0 min' AND interval '20 min'
 ORDER BY c.scheduled_at
 LIMIT :limit
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
    import os
    from datetime import timedelta, timezone

    # Локальный .env нужен только чтобы поднялись импорты приложения (ключи
    # API, хранилище вложений). Подключение к базе после него перебивается на
    # ПРОД — иначе скрипт молча посмотрит dev-данные и ничего не докажет.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()
    sys.path.insert(0, str(PROJECT_ROOT))

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services import homework_service

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    msk = timezone(timedelta(hours=3))

    def fmt(dt) -> str:
        return dt.astimezone(msk).strftime("%d.%m %H:%M")

    ok = True
    async with factory() as db:
        rows = (
            await db.execute(text(_PAIRS_SQL), {"limit": LIMIT})
        ).mappings().fetchall()
        if not rows:
            print("Сдвоенных пар не найдено.")
            await engine.dispose()
            return 0

        print(f"Сдвоенных пар для проверки: {len(rows)}\n")
        for r in rows:
            # Срок, который поставит выдача после ПЕРВОЙ пары.
            due = await homework_service.next_due_for(
                db, student_id=int(r["student_id"]), after=r["first_at"],
                now=r["first_at"] + timedelta(minutes=int(r["duration_minutes"])),
            )
            paired = due <= r["next_at"]
            if paired:
                ok = False
            print(
                f"{r['full_name']}: пара {fmt(r['first_at'])} + "
                f"{fmt(r['next_at'])} -> срок {fmt(due)}"
                f" {'❌ попал на вторую пару' if paired else '✓ за пределами блока'}"
            )

    await engine.dispose()
    print("\nИТОГ:", "все сроки за пределами блока" if ok else "ЕСТЬ ПОПАДАНИЯ В БЛОК")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
