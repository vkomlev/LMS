# -*- coding: utf-8 -*-
"""tsk-798: какой объём программы получат живые ученики. ТОЛЬКО ЧТЕНИЕ.

Зовёт настоящий `program_scope_service.compute_scope` на боевых данных и
показывает, сколько ядра и сколько тренажёра помещается каждому ученику до его
срока. Ничего не сохраняет: ни планов, ни настроек — только SELECT.

Запуск: `python scripts/tsk798_preview_program_scope.py`
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

_STUDENTS_SQL = """
SELECT DISTINCT u.id, u.full_name
  FROM user_courses uc
  JOIN users u ON u.id = uc.user_id
 WHERE uc.is_active = true AND uc.course_id = ANY(:course_ids)
 ORDER BY u.full_name
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


async def main() -> None:
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
        ids = vol._course_ids(
            settings_store.get_str("homework_program_ege_courses")
        ) + vol._course_ids(settings_store.get_str("homework_program_oge_courses"))
        rows = (
            await db.execute(text(_STUDENTS_SQL), {"course_ids": ids})
        ).mappings().all()

        print(f"Учеников программ: {len(rows)}\n")
        print(
            f"{'ученик':<28}{'прог':>5}{'нед':>6}{'темп':>6}"
            f"{'ядро':>7}{'тренаж':>8}{'дают':>7}{'доля':>7}  ядро"
        )
        trimmed = full = partial = 0
        for r in rows:
            student_id = int(r["id"])
            plan = await vol.compute(db, student_id=student_id)
            if plan.program_kind is None:
                continue
            program = await vol.program_for_student(
                db, student_id=student_id, grade=plan.grade,
                today=__import__("datetime").date.today(),
            )
            if program is None:
                continue
            scope = await scope_service.compute_scope(
                db, student_id=student_id, kind=program["kind"],
                root_ids=program["root_ids"], deadline=program["deadline"],
                fact_per_week=plan.fact_per_week,
            )
            name = (r["full_name"] or f"#{student_id}")[:27]
            print(
                f"{name:<28}{scope.kind:>5}{scope.weeks_left:>6}"
                f"{scope.planned_pace:>6}{scope.core_total:>7}"
                f"{scope.drill_total:>8}{scope.drill_allowed:>7}"
                f"{scope.drill_ratio:>7.0%}  "
                f"{'НЕ ВЛЕЗАЕТ' if scope.core_trimmed else 'ок'}"
            )
            if scope.core_trimmed:
                trimmed += 1
            elif scope.fits_fully:
                full += 1
            else:
                partial += 1

        print(
            f"\nПрограмма целиком: {full}; ядро + часть тренажёра: {partial}; "
            f"ядро не помещается: {trimmed}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
