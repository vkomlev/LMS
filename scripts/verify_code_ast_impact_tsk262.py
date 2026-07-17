"""tsk-262: read-only проверка влияния — что изменится на проде после правки.

Прогоняет ВСЕ реальные сдачи по отбираемым заданиям через НАСТОЯЩИЙ движок
(CheckingService._matches_short_answer) дважды:
  ДО    — normalization как сейчас в проде;
  ПОСЛЕ — normalization без 'lower' и с 'code_ast' (то, что сделает
          scripts/set_code_ast_flag_tsk262.py --apply).
Для каждой изменившейся сдачи печатает ответ, эталон и причину.

Ничего не пишет. Запуск:
  PYTHONIOENCODING=utf-8 PYTHONPATH=. DATABASE_URL="…5.42.107.253…" \
    ./.venv/Scripts/python.exe scripts/verify_code_ast_impact_tsk262.py
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import List

import asyncpg

import importlib.util
import pathlib

from app.services.checking_service import CheckingService

# Классификатор берём из скрипта простановки флага, чтобы замер и правка
# не разъехались. Импорт по пути — scripts/ не пакет.
_spec = importlib.util.spec_from_file_location(
    "set_code_ast_flag_tsk262",
    pathlib.Path(__file__).with_name("set_code_ast_flag_tsk262.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
is_python_code = _mod.is_python_code

SELECT_TASKS = """
SELECT t.id,
       t.external_uid,
       t.solution_rules->'short_answer'->'normalization'    AS normalization,
       t.solution_rules->'short_answer'->'accepted_answers' AS accepted
FROM tasks t
WHERE t.is_active AND t.solution_rules->'short_answer' IS NOT NULL
ORDER BY t.id
"""

SELECT_RESULTS = """
SELECT r.id, r.task_id, r.user_id, r.is_correct,
       r.answer_json->'response'->>'value' AS value
FROM task_results r
WHERE r.task_id = ANY($1::int[])
  AND r.answer_json->'response'->>'value' IS NOT NULL
ORDER BY r.task_id, r.id
"""


def steps_after(steps_before: List[str]) -> List[str]:
    """Нормализация, какой она станет после правки данных."""
    return [s for s in steps_before if s not in ("lower", "code_ast")] + ["code_ast"]


def passes(value: str, accepted: List[dict], steps: List[str]) -> bool:
    return any(
        CheckingService._matches_short_answer(value, a["value"], steps) for a in accepted
    )


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError("нужен прод-DSN из .mcp.json")
    conn = await asyncpg.connect(dsn)
    try:
        tasks = {}
        for r in await conn.fetch(SELECT_TASKS):
            accepted = json.loads(r["accepted"] or "[]")
            values = [a["value"] for a in accepted]
            if values and all(is_python_code(v) for v in values):
                tasks[r["id"]] = {
                    "uid": r["external_uid"],
                    "before": json.loads(r["normalization"] or "[]"),
                    "accepted": accepted,
                }
        print(f"Заданий в наборе code_ast: {len(tasks)}")

        results = await conn.fetch(SELECT_RESULTS, list(tasks))
        print(f"Реальных сдач по ним: {len(results)}")

        before_pass = after_pass = 0
        changes = []
        for r in results:
            t = tasks[r["task_id"]]
            sb, sa = t["before"], steps_after(t["before"])
            b = passes(r["value"], t["accepted"], sb)
            a = passes(r["value"], t["accepted"], sa)
            before_pass += b
            after_pass += a
            if a != b:
                changes.append((r, t, sb, sa, b, a))

        print(f"Зачётов ДО:    {before_pass}")
        print(f"Зачётов ПОСЛЕ: {after_pass}")
        print(f"\n--- Изменившихся вердиктов: {len(changes)} ---")
        for r, t, sb, sa, b, a in changes:
            print(f"\n  result={r['id']} task={r['task_id']} ({t['uid']}) user={r['user_id']}")
            print(f"    ответ  : {r['value']!r}")
            print(f"    эталоны: {[x['value'] for x in t['accepted']]!r}")
            print(f"    было {'ЗАЧЁТ' if b else 'НЕЗАЧЁТ'} → стало {'ЗАЧЁТ' if a else 'НЕЗАЧЁТ'}")
            print(f"    steps: {sb} → {sa}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
