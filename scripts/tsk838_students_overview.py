# -*- coding: utf-8 -*-
"""Сводная таблица по ученикам программ подготовки. ТОЛЬКО ЧТЕНИЕ.

Запрос оператора 09.09: одним взглядом увидеть по каждому — класс, где он
сейчас, сколько осталось, с какой скоростью идёт, сколько нужно и насколько
подрезан объём программы.

Числа берутся из НАСТОЯЩИХ сервисов (`homework_volume_service`,
`program_scope_service`, `manual_progress_service`), а не считаются заново:
таблица должна показывать ровно то, что видят преподаватель в сводке и родитель
на дашборде. Свой расчёт здесь означал бы третий ответ на тот же вопрос.

Ничего не пишет. Результат — CSV рядом с указанным путём и печать в консоль.

Запуск: `python scripts/tsk838_students_overview.py [--csv путь.csv]`
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#: Ученики программ подготовки — записанные на любой корневой курс из настроек.
_STUDENTS_SQL = """
SELECT DISTINCT u.id, u.full_name, u.school_grade
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


async def _current_topic(db, *, student_id: int, root_ids: list[int]) -> str:
    """Тема, на которой ученик сейчас: первый незавершённый элемент программы.

    Берётся из того же дерева прогресса, что показывает кабинет, — иначе в
    отчёте была бы «своя» тема, не совпадающая с экраном ученика.
    """
    from app.services import manual_progress_service

    DONE = ("PASSED", "COMPLETED", "SKIPPED")
    for course_id in root_ids:
        progress = await manual_progress_service.get_student_progress(
            db, student_id=student_id, course_id=int(course_id)
        )
        items = progress.get("items", [])
        sections = {
            i["item_id"]: i["title"] for i in items if i["item_type"] == "course"
        }
        for i in items:
            if i["item_type"] not in ("task", "material"):
                continue
            if i["status"] in DONE:
                continue
            parent = i.get("parent_course_id")
            section = sections.get(parent) if parent else None
            return section or (i.get("title") or "—")
    return "программа пройдена"


async def main(csv_path: Path | None) -> int:
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
    rows_out: list[dict[str, object]] = []

    async with factory() as db:
        await settings_store.refresh(db)
        course_ids = vol._course_ids(
            settings_store.get_str("homework_program_ege_courses")
        ) + vol._course_ids(settings_store.get_str("homework_program_oge_courses"))

        students = (
            await db.execute(text(_STUDENTS_SQL), {"course_ids": course_ids})
        ).mappings().all()

        for r in students:
            student_id = int(r["id"])
            plan = await vol.compute(db, student_id=student_id)
            program = await vol.program_for_student(
                db, student_id=student_id, grade=plan.grade, today=date.today()
            )
            if program is None:
                continue

            scope = await scope_service.compute_scope(
                db,
                student_id=student_id,
                kind=program["kind"],
                root_ids=program["root_ids"],
                deadline=program["deadline"],
                fact_per_week=plan.fact_per_week,
            )
            topic = await _current_topic(
                db, student_id=student_id, root_ids=program["root_ids"]
            )

            # Насколько программа подрезана: сколько её осталось от полной.
            full = scope.core_total + scope.drill_total
            kept = scope.core_total + scope.drill_allowed
            trim_pct = round((1 - kept / full) * 100) if full else 0

            # Норматив, который РЕАЛЬНО нужен по подрезанной программе. Система
            # сейчас показывает норматив от ПОЛНОГО остатка и про сокращение не
            # знает (tsk-798 подрезает объём, tsk-797 считает норму — они не
            # связаны). У Крук это «нужно 41», при том что программа ей
            # спланирована на 870 элементов, то есть 30 в неделю.
            weeks = scope.weeks_left or 1
            honest = round(kept / weeks) if weeks else plan.target_per_week

            rows_out.append(
                {
                    "ФИО": r["full_name"],
                    "класс": r["school_grade"] or "",
                    "программа": scope.kind,
                    "тема сейчас": topic,
                    "осталось решить": plan.program_tasks_remaining,
                    "осталось всего": plan.remaining_items,
                    "темп в неделю": plan.fact_per_week,
                    "норматив показанный": plan.target_per_week,
                    "норматив по факту": honest,
                    "задаём в неделю": plan.volume_per_week,
                    "сокращение, %": trim_pct,
                    "срок": program["deadline"].isoformat(),
                }
            )

    await engine.dispose()

    rows_out.sort(key=lambda x: (-int(x["сокращение, %"]), str(x["ФИО"])))

    head = (
        f"{'ФИО':<28}{'кл':>3}{'прог':>5}  {'тема сейчас':<34}"
        f"{'ост':>6}{'темп':>6}{'норма':>7}{'факт-норма':>11}{'даём':>6}{'сокр':>6}"
    )
    print(head)
    print("-" * len(head))
    for x in rows_out:
        print(
            f"{str(x['ФИО'])[:27]:<28}{str(x['класс']):>3}{str(x['программа']):>5}  "
            f"{str(x['тема сейчас'])[:33]:<34}"
            f"{x['осталось решить']:>6}{x['темп в неделю']:>6}"
            f"{x['норматив показанный']:>7}{x['норматив по факту']:>11}"
            f"{x['задаём в неделю']:>6}"
            f"{str(x['сокращение, %']) + '%':>6}"
        )
    print(f"\nВсего учеников программ: {len(rows_out)}")

    if csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig: Excel без BOM открывает кириллицу как мусор.
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=None)
    sys.exit(asyncio.run(main(parser.parse_args().csv)))
