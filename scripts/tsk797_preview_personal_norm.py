# -*- coding: utf-8 -*-
"""tsk-797: какие персональные нормы даст новый расчёт на боевых данных. ЧТЕНИЕ.

Замечание оператора 04.09: норма показывала всем одиннадцатиклассникам 20 в
неделю независимо от прогресса. Скрипт зовёт НАСТОЯЩИЙ `homework_volume_service`
на боевой базе и печатает, что получилось у каждого ученика программ подготовки:
остаток программы, срок, «надо в неделю» и итоговую выдачу.

Ничего не пишет: ни выдач, ни настроек — только SELECT.

Запуск: `python scripts/tsk797_preview_personal_norm.py`
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

#: Ученики программ подготовки — записанные на любой корневой курс из настроек.
_STUDENTS_SQL = """
SELECT DISTINCT u.id, u.full_name, u.school_grade
  FROM user_courses uc
  JOIN users u ON u.id = uc.user_id
 WHERE uc.is_active = true
   AND uc.course_id = ANY(:course_ids)
 ORDER BY u.full_name
"""


async def main() -> None:
    # Локальный .env нужен только чтобы поднялись импорты приложения.
    # Подключение к базе после него перебивается на ПРОД — иначе скрипт молча
    # посмотрит dev-данные и ничего не докажет.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import settings_store
    from app.services import homework_volume_service as vol

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

        print(f"Курсы программ: {ids}; учеников: {len(rows)}\n")
        print(
            f"{'ученик':<28}{'кл':>3}{'прог':>6}{'срок':>12}"
            f"{'остаток':>9}{'надо':>6}{'делает':>8}{'задаём':>8}"
        )
        buckets: dict[str, int] = {}
        for r in rows:
            plan = await vol.compute(db, student_id=int(r["id"]))
            name = (r["full_name"] or f"#{r['id']}")[:27]
            grade = r["school_grade"] or "-"
            kind = plan.program_kind or "-"
            deadline = (
                plan.program_deadline.isoformat() if plan.program_deadline else "-"
            )
            print(
                f"{name:<28}{str(grade):>3}{kind:>6}{deadline:>12}"
                f"{plan.remaining_items:>9}{plan.target_per_week:>6}"
                f"{plan.fact_per_week:>8}{plan.volume_per_week:>8}"
            )
            key = (
                "успевает" if plan.target_per_week <= 12
                else "напряжённо" if plan.target_per_week <= 25
                else "не успевает"
            )
            buckets[key] = buckets.get(key, 0) + 1
        print("\nПо посильности нормы:", buckets)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
