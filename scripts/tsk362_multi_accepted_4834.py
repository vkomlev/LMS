# -*- coding: utf-8 -*-
"""tsk-362: задание 4834 (Черепаха) — несколько допустимых вариантов ответа.

ЗАЧЕМ
Условие само перечисляет, что засчитывается: «принимается любой верный вариант: up, penup,
up(), penup() и другие». Это единственное задание в разборе, где автор заранее объявил набор
ответов, а не одно значение, — поэтому и правило собирается со списком `accepted_answers`,
который движок проверяет как «подошёл любой».

Список: `up`, `penup`, `pu` (короткий псевдоним turtle) и те же три со скобками. Нормализация
`trim` + `lower`, поэтому «PenUp()» и « penup() » тоже засчитаются.

Решение оператора 2026-07-22.

Запуск: dry-run по умолчанию;
  python scripts/tsk362_multi_accepted_4834.py
  DBCHECK_OK=1 python scripts/tsk362_multi_accepted_4834.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.solution_rules import (  # noqa: E402
    SolutionRules,
    ShortAnswerRules,
    ShortAnswerAccepted,
)

TASK_ID = 4834
VARIANTS = ["up", "penup", "pu", "up()", "penup()", "pu()"]

GUARD = """
  AND is_active
  AND (solution_rules->>'manual_review_required')::bool IS TRUE
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) = 0
"""


def _dsn() -> str:
    env = os.environ.get("LEARN_PROD_DSN") or os.environ.get("DATABASE_URL", "")
    dsn = env.replace("postgresql+asyncpg://", "postgresql://")
    if "5.42.107.253" not in dsn:
        for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
            if not candidate.exists():
                continue
            cfg = json.loads(candidate.read_text(encoding="utf-8"))
            servers = cfg.get("mcpServers", cfg)
            for arg in servers["learn_prod_db"]["args"]:
                if isinstance(arg, str) and arg.startswith("postgresql://") and "5.42.107.253" in arg:
                    dsn = arg
                    break
    if "5.42.107.253" not in dsn or "/learn" not in dsn:
        raise RuntimeError("Не нашёл прод-DSN learn (5.42.107.253/learn).")
    return dsn


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"SELECT id, max_score, left(regexp_replace(task_content->>'stem','<[^>]+>',' ','g'), 70) AS s "
                f"FROM tasks WHERE id = $1 {GUARD}", TASK_ID)
            if row is None:
                raise AssertionError(f"{TASK_ID}: уже не на ручной проверке или есть ответ — не трогаю")
            print(f"ДО: id={row['id']} «{row['s'].strip()}…»")

            ms = row["max_score"] or 1
            rules = SolutionRules(
                max_score=ms, scoring_mode="all_or_nothing", auto_check=True,
                manual_review_required=False,
                short_answer=ShortAnswerRules(
                    normalization=["trim", "lower"],
                    accepted_answers=[ShortAnswerAccepted(value=v, score=ms) for v in VARIANTS],
                ),
            ).model_dump()
            res = await conn.execute(
                f"UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1 {GUARD}",
                TASK_ID, json.dumps(rules))
            if int(res.split()[-1]) != 1:
                raise AssertionError(f"обновлено {res}")

            check = await conn.fetchrow(
                "SELECT (solution_rules->>'manual_review_required')::bool AS manual, "
                "solution_rules#>'{short_answer,accepted_answers}' AS acc, "
                "(solution_rules->>'max_score')::int AS ms, max_score FROM tasks WHERE id = $1", TASK_ID)
            values = [a["value"] for a in json.loads(check["acc"])]
            print(f"ПОСЛЕ: ручная={check['manual']} варианты={values}")
            if check["manual"] or values != VARIANTS or check["ms"] != check["max_score"]:
                raise AssertionError("состояние после записи неверное")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
