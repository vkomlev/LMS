# -*- coding: utf-8 -*-
"""tsk-798: применяется ли персональный объём в обходе. ТОЛЬКО ЧТЕНИЕ.

Зовёт настоящий `LearningEngineService._effective_task_rows` на боевых данных и
сравнивает, сколько заданий в подкурсе всего и сколько из них видит ученик с
планом. Ничего не пишет: только SELECT.

Запуск: `python scripts/tsk798_check_engine_scope.py <student_id>`
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


async def main(student_id: int) -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["DATABASE_URL"] = load_prod_dsn_asyncpg_style()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.services.learning_engine_service import LearningEngineService
    from app.services import program_scope_service

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    service = LearningEngineService()

    async with factory() as db:
        thresholds = await program_scope_service.thresholds_for(
            db, student_id=student_id
        )
        if not thresholds:
            print(f"У ученика {student_id} плана нет — сравнивать не с чем.")
            await engine.dispose()
            return 1

        print(f"Подкурсов в плане: {len(thresholds)}\n")
        print(f"{'подкурс':>8}{'всего':>8}{'видит':>8}{'порог':>8}  название")
        total_all = total_seen = 0
        for course_id, threshold in sorted(thresholds.items()):
            title = (
                await db.execute(
                    text("SELECT title FROM courses WHERE id = :c"), {"c": course_id}
                )
            ).scalar() or "?"
            all_rows = await service._ordered_task_rows(db, course_id)
            seen = await service._effective_task_rows(db, course_id, student_id)
            total_all += len(all_rows)
            total_seen += len(seen)
            print(
                f"{course_id:>8}{len(all_rows):>8}{len(seen):>8}{threshold:>8}"
                f"  {title[:44]}"
            )

        print(f"\nВсего заданий в подкурсах плана: {total_all}")
        print(f"Ученик видит: {total_seen}")
        if total_seen >= total_all:
            print("ВЫБОРКА НЕ ПРИМЕНИЛАСЬ — ученик видит всё")
            await engine.dispose()
            return 1
        print(f"Скрыто выборкой: {total_all - total_seen}")

        # Решённое обязано остаться видимым: иначе числитель прогресса
        # превысит знаменатель и подкурс не закроется никогда.
        solved = set(
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT tr.task_id FROM task_results tr "
                        "  JOIN attempts a ON a.id = tr.attempt_id "
                        "   AND a.cancelled_at IS NULL "
                        " WHERE tr.user_id = :sid AND tr.is_correct"
                    ),
                    {"sid": student_id},
                )
            ).scalars().all()
        )
        seen_ids: set[int] = set()
        for course_id in thresholds:
            seen_ids |= {i for i, _ in await service._effective_task_rows(
                db, course_id, student_id
            )}
        in_plan_solved = solved & {
            int(i)
            for course_id in thresholds
            for i, _ in await service._ordered_task_rows(db, course_id)
        }
        lost = in_plan_solved - seen_ids
        print(
            f"Решённых заданий в подкурсах плана: {len(in_plan_solved)}; "
            f"потеряно выборкой: {len(lost)}"
        )
        if lost:
            print("ОШИБКА: выборка выбросила уже решённое", sorted(lost)[:10])
            await engine.dispose()
            return 1

    await engine.dispose()
    print("\nИТОГ: объём применяется, решённое на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]))))
