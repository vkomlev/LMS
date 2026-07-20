# -*- coding: utf-8 -*-
"""tsk-317 срочный фикс: v11t26 записан с НЕВЕРНЫМ ответом.

Найдено при повторной сверке таблицы ответов (страница 247 книги, "Окончание
табл.", графа «26» для варианта 11) в высоком разрешении: колонки задания 26
(два числа) и задания 27 (четыре числа, две строки) были перепутаны при
первой транскрипции — в БД записано первое число из ДВУХ строк задания 27
("108420 16507") вместо реального ответа задания 26 ("29 49").

Реальная строка варианта 11 на странице: 19=51, 20=(46,50), 21=45, 22=19,
23=961, 24=5678, **26=(29,49)**, 27=(108420,16507,88,399).
Task 27 в LMS не заводится (не входит в охват), поэтому это не задевает
других заданий — только 26.

Запуск: dry-run по умолчанию; --apply при DBCHECK_OK=1.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import asyncpg

project_root = Path(__file__).resolve().parents[1]


def _dsn() -> str:
    cfg = json.loads((project_root / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers", cfg)
    for arg in servers["learn_prod_db"]["args"]:
        if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
            return arg
    raise RuntimeError("prod DSN не найден в .mcp.json")


TASK_ID = 4585  # crylov:v11t26
WRONG_VALUE = "108420 16507"
CORRECT_VALUE = "29 49"


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, external_uid, solution_rules FROM tasks WHERE id=$1", TASK_ID
            )
            if row is None:
                raise RuntimeError(f"id={TASK_ID} не найден")
            rules = json.loads(row["solution_rules"])
            current = rules["short_answer"]["accepted_answers"][0]["value"]
            print(f"Текущее значение id={TASK_ID} ({row['external_uid']}): {current!r}")
            if current != WRONG_VALUE:
                raise RuntimeError(
                    f"ожидал текущее значение {WRONG_VALUE!r}, а в БД {current!r} — "
                    "возможно, уже исправлено или изменилось; проверь вручную"
                )
            rules["short_answer"]["accepted_answers"][0]["value"] = CORRECT_VALUE
            await conn.execute(
                "UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1",
                TASK_ID, json.dumps(rules),
            )
            check = await conn.fetchval(
                "SELECT solution_rules#>>'{short_answer,accepted_answers,0,value}' FROM tasks WHERE id=$1",
                TASK_ID,
            )
            if check != CORRECT_VALUE:
                raise AssertionError(f"верификация не сошлась: {check!r} != {CORRECT_VALUE!r}")
            print(f"OK id={TASK_ID}: {WRONG_VALUE!r} -> {CORRECT_VALUE!r}")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main("--apply" in sys.argv))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
