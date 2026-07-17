"""tsk-262: read-only замер AST-нормализации ответа-кода на прод-данных.

Ничего не пишет. Отвечает на четыре вопроса карточки задачи:
  1. Сколько эталонов код-заданий не парсится (находка «обрезанный эталон»)?
  2. Как AST-канон меняет вердикт на реальных сдачах и ПОЧЕМУ каждый?
  3. Какие ответы учеников не парсятся (нужен ли fallback на текст)?
  4. Сколько «код-заданий» по регулярке на самом деле не код?

Сравниваются три движка:
  - current — то, что на проде сейчас (_normalize_text с текущей normalization задания);
  - naive   — голый ast.unparse(ast.parse(v)) без fallback (замер из карточки: 23 vs 36);
  - proposed — AST-канон, если парсятся ОБЕ стороны, иначе fallback на current.

Запуск (DSN прода из .mcp.json, не из .env):
  PYTHONIOENCODING=utf-8 DATABASE_URL="postgresql://…5.42.107.253:5432/learn" \
    ./.venv/Scripts/python.exe scripts/measure_ast_normalization_tsk262.py
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from app.services.checking_service import CheckingService

# Та же регулярка-классификатор, что в tsk-261 — берём ТОТ ЖЕ набор заданий,
# чтобы разведочный замер был сопоставим с предыдущим.
# ВАЖНО: регулярка дефектна (в PG `\b` — backspace, а не граница слова), поэтому
# набор «153 задания» НЕ включает задания с голым `import X`. Здесь это осознанно:
# цель — воспроизвести прежнюю выборку один-в-один. Для отбора заданий под флаг
# используется НЕ она, а классификация разбором в AST
# (scripts/set_code_ast_flag_tsk262.py::is_python_code). Разбор — docs/ai/ERRORS.md,
# запись 2026-07-17. Не копировать эту регулярку в новый код.
CODE_RX = r"\(\)|\(.*\)|\.[a-zA-Z_]|=|\bprint\b|\bimport\b|\bdef\b|\[.*\]"

SELECT_TASKS = """
SELECT t.id,
       t.external_uid AS code,
       t.solution_rules->'short_answer'->'normalization'    AS normalization,
       t.solution_rules->'short_answer'->'accepted_answers' AS accepted
FROM tasks t
WHERE t.is_active
  AND t.solution_rules->'short_answer' IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements(t.solution_rules->'short_answer'->'accepted_answers') a
    WHERE a->>'value' ~ $1
  )
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


def canon_code(value: str) -> Optional[str]:
    """Канон программы через AST. None — если это не разбираемый Python."""
    try:
        return ast.unparse(ast.parse(value.strip()))
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


def norm_text(value: str, steps: List[str]) -> str:
    """Текстовая нормализация ровно тем же кодом, что работает на проде."""
    return CheckingService._normalize_text(value, steps)


def verdict_current(value: str, accepted: List[dict], steps: List[str]) -> bool:
    vn = norm_text(value, steps)
    return any(vn == norm_text(a["value"], steps) for a in accepted)


def verdict_naive(value: str, accepted: List[dict]) -> bool:
    """Голый AST: не распарсилось — незачёт (так мерили в карточке)."""
    cv = canon_code(value)
    if cv is None:
        return False
    return any(canon_code(a["value"]) == cv for a in accepted)


def verdict_proposed(value: str, accepted: List[dict], steps: List[str]) -> Tuple[bool, str]:
    """AST-канон при разбираемости обеих сторон, иначе fallback на текст.

    Возвращает (вердикт, каким путём получен) — путь нужен для объяснения диффа.
    """
    cv = canon_code(value)
    if cv is not None:
        for a in accepted:
            ca = canon_code(a["value"])
            if ca is not None and ca == cv:
                return True, "ast"
    # Ни одна пара не сошлась по AST (или что-то не парсится) — текстовый путь.
    return verdict_current(value, accepted, steps), "text-fallback"


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        raise RuntimeError("нужен прод-DSN из .mcp.json (в .env лежит localhost)")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(SELECT_TASKS, CODE_RX)
        tasks: Dict[int, dict] = {}
        for r in rows:
            tasks[r["id"]] = {
                "code": r["code"],
                "steps": json.loads(r["normalization"]) if r["normalization"] else [],
                "accepted": json.loads(r["accepted"]) if r["accepted"] else [],
            }
        print(f"Код-заданий по регулярке CODE_RX: {len(tasks)}")

        # --- 1. Эталоны: что не парсится ---
        broken: List[Tuple[int, str, str]] = []
        all_ok = 0
        for tid, t in tasks.items():
            bad = [a["value"] for a in t["accepted"] if canon_code(a["value"]) is None]
            if bad:
                broken.append((tid, t["code"], bad[0]))
            else:
                all_ok += 1
        print(f"  эталоны парсятся полностью: {all_ok}")
        print(f"  есть неразбираемый эталон:   {len(broken)}")
        print("\n--- Задания с неразбираемым эталоном ---")
        for tid, code, sample in sorted(broken):
            print(f"  task={tid:<6} {code:<28} {sample[:70]!r}")

        # --- 2/3. Сдачи ---
        results = await conn.fetch(SELECT_RESULTS, list(tasks))
        print(f"\nРеальных сдач по этим заданиям: {len(results)}")

        cnt = Counter()
        changes: List[dict] = []
        unparsed_answers: List[Tuple[int, str]] = []
        for r in results:
            t = tasks[r["task_id"]]
            value = r["value"]
            cur = verdict_current(value, t["accepted"], t["steps"])
            naive = verdict_naive(value, t["accepted"])
            prop, path = verdict_proposed(value, t["accepted"], t["steps"])
            cnt["current_pass"] += cur
            cnt["naive_pass"] += naive
            cnt["proposed_pass"] += prop
            if canon_code(value) is None:
                cnt["answer_unparsed"] += 1
                unparsed_answers.append((r["task_id"], value))
            if prop != cur:
                changes.append(
                    {
                        "result_id": r["id"],
                        "task_id": r["task_id"],
                        "task_code": t["code"],
                        "user_id": r["user_id"],
                        "value": value,
                        "accepted": [a["value"] for a in t["accepted"]],
                        "steps": t["steps"],
                        "current": cur,
                        "naive": naive,
                        "proposed": prop,
                        "path": path,
                    }
                )

        print(f"  зачётов current : {cnt['current_pass']}")
        print(f"  зачётов naive   : {cnt['naive_pass']}")
        print(f"  зачётов proposed: {cnt['proposed_pass']}")
        print(f"  ответов, не разбираемых как Python: {cnt['answer_unparsed']}")

        print("\n--- Ответы учеников, которые не парсятся (нужен fallback) ---")
        for tid, v in unparsed_answers:
            print(f"  task={tid:<6} {v[:70]!r}")

        print(f"\n--- Изменившиеся вердикты (proposed vs current): {len(changes)} ---")
        for c in changes:
            direction = "НОВЫЙ ЗАЧЁТ" if c["proposed"] else "СНЯТ ЗАЧЁТ"
            print(
                f"\n  [{direction}] result={c['result_id']} task={c['task_id']} "
                f"({c['task_code']}) user={c['user_id']} путь={c['path']}"
            )
            print(f"    ответ  : {c['value']!r}")
            print(f"    эталоны: {c['accepted']!r}")
            print(f"    steps  : {c['steps']}  | naive={c['naive']}")

        out = os.path.join("reviews", "evidence", "tsk262_ast_measure.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "tasks_total": len(tasks),
                    "refs_broken": [
                        {"task_id": t, "code": c, "sample": s} for t, c, s in sorted(broken)
                    ],
                    "results_total": len(results),
                    "counters": dict(cnt),
                    "unparsed_answers": [{"task_id": t, "value": v} for t, v in unparsed_answers],
                    "changes": changes,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nПодробности: {out}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
