# -*- coding: utf-8 -*-
"""tsk-740, партия 12: снимок блока 23 перед переходом на рекурсию с запоминанием.

Только чтение. Сохраняет в reviews/tsk740-block23-snapshot-<метка>/:
  - materials.json — все материалы курсов 1490–1495 целиком (id, курс, позиция, content);
  - tasks.json     — все задания блока (id, курс, task_content, solution_rules);
  - courses.json   — строки курсов блока и их связи с родителями.
Снимок служит и исходником для правки текстов, и точкой отката.

Запуск: python scripts/tsk740_block23_snapshot.py [--metka до]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk740_gen23_graphs import _dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

KURSY = [1490, 1491, 1492, 1493, 1494]
PAPKA = Path(__file__).resolve().parents[1] / "reviews"


async def snyat(metka: str) -> Path:
    """Снять снимок блока и вернуть папку с файлами."""
    conn = await asyncpg.connect(_dsn())
    try:
        # Лист вариаций появится в этой партии — берём его по uid, если он уже есть.
        var_id = await conn.fetchval(
            "SELECT id FROM courses WHERE course_uid = 'lms:tsk740:ege2027:23:var'"
        )
        kursy = KURSY + ([var_id] if var_id else [])
        materialy = await conn.fetch(
            "SELECT id, course_id, order_position, title, type::text AS type, external_uid, "
            "requirement_level, is_active, content::text AS content FROM materials "
            "WHERE course_id = ANY($1::int[]) ORDER BY course_id, order_position",
            kursy,
        )
        zadaniya = await conn.fetch(
            "SELECT id, course_id, external_uid, difficulty_id, requirement_level, is_active, "
            "task_content::text AS task_content, solution_rules::text AS solution_rules "
            "FROM tasks WHERE course_id = ANY($1::int[]) ORDER BY course_id, id",
            kursy,
        )
        kursy_st = await conn.fetch(
            "SELECT c.id, c.title, c.description, c.course_uid, c.is_required, c.is_active, "
            "cp.parent_course_id, cp.order_number FROM courses c "
            "LEFT JOIN course_parents cp ON cp.course_id = c.id "
            "WHERE c.id = ANY($1::int[]) ORDER BY c.id",
            kursy,
        )
    finally:
        await conn.close()

    papka = PAPKA / f"tsk740-block23-snapshot-{metka}"
    papka.mkdir(parents=True, exist_ok=True)

    def v_json(stroki: list, razobrat: tuple[str, ...]) -> list[dict]:
        """Строки базы -> словари; jsonb-поля разворачиваются из текста."""
        out = []
        for r in stroki:
            d = dict(r)
            for pole in razobrat:
                if d.get(pole) is not None:
                    d[pole] = json.loads(d[pole])
            out.append(d)
        return out

    (papka / "materials.json").write_text(
        json.dumps(v_json(materialy, ("content",)), ensure_ascii=False, indent=1), encoding="utf-8")
    (papka / "tasks.json").write_text(
        json.dumps(v_json(zadaniya, ("task_content", "solution_rules")), ensure_ascii=False, indent=1),
        encoding="utf-8")
    (papka / "courses.json").write_text(
        json.dumps(v_json(kursy_st, ()), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("Снимок: материалов %d, заданий %d, курсов %d -> %s",
             len(materialy), len(zadaniya), len(kursy_st), papka)
    return papka


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Снимок блока 23 (только чтение)")
    parser.add_argument("--metka", default="do", help="метка снимка в имени папки")
    asyncio.run(snyat(parser.parse_args().metka))
