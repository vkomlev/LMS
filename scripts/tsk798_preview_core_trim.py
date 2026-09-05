# -*- coding: utf-8 -*-
"""tsk-798: что отрежется у позднего старта. ТОЛЬКО ЧТЕНИЕ.

Резка ядра нужна тем, кто придёт после Нового года; на сегодняшнем составе
учеников она не срабатывает ни разу (ядро помещается у всех). Чтобы проверить
её на боевых данных, а не на выдуманных, скрипт считает настоящим
`program_scope_service.compute_scope` объём РЕАЛЬНОГО ученика, подставляя
разные даты старта — то есть разное число недель до 31 марта.

Ничего не пишет: ни планов, ни приоритетов — только SELECT.

Запуск: `python scripts/tsk798_preview_core_trim.py <student_id>`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#: С каких дат смотрим: ученик, пришедший в этот день, до 31 марта 2027.
STARTS = [
    date(2026, 10, 1),
    date(2026, 11, 1),
    date(2026, 12, 1),
    date(2027, 1, 15),
    date(2027, 2, 1),
    date(2027, 3, 1),
]


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


async def main(student_id: int) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import settings_store
    from app.services import homework_volume_service as vol
    from app.services import program_scope_service as scope_service

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        await settings_store.refresh(db)
        plan = await vol.compute(db, student_id=student_id)
        program = await vol.program_for_student(
            db, student_id=student_id, grade=plan.grade, today=date.today()
        )
        if program is None:
            print(f"Ученик {student_id} вне программ подготовки.")
            await engine.dispose()
            return 1

        name = (
            await db.execute(
                text("SELECT full_name FROM users WHERE id = :u"), {"u": student_id}
            )
        ).scalar()
        print(f"Ученик: {name} ({program['kind']}), срок {program['deadline']}\n")

        for start in STARTS:
            scope = await scope_service.compute_scope(
                db,
                student_id=student_id,
                kind=program["kind"],
                root_ids=program["root_ids"],
                deadline=program["deadline"],
                fact_per_week=plan.fact_per_week,
                today=start,
            )
            titles: list[str] = []
            if scope.excluded_courses:
                titles = [
                    str(t)
                    for t in (
                        await db.execute(
                            text(
                                "SELECT title FROM courses WHERE id = ANY(:ids) "
                                " ORDER BY program_priority"
                            ),
                            {"ids": sorted(scope.excluded_courses)},
                        )
                    ).scalars().all()
                ]
            numbers = [
                t.split(".")[0].replace("ЕГЭ по информатике", "").strip()
                for t in titles
            ]
            print(
                f"старт {start:%d.%m.%Y}: недель {scope.weeks_left:>5}, "
                f"ядро {scope.core_total:>4}, отработка {scope.drill_allowed:>4}"
                f"/{scope.drill_total:<4} "
                + (
                    f"выпало {len(titles)}: {', '.join(numbers)}"
                    if titles
                    else ("ядро не помещается, резать нечего" if scope.core_trimmed
                          else "программа целиком")
                )
            )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]))))
