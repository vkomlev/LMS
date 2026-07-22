# -*- coding: utf-8 -*-
"""tsk-367: снять ответы, унаследованные от партии Крылова (брак OCR), — 3 задания из tsk-362.

ПОЧЕМУ
В [[tsk-362]] ответ разрешалось брать у «близнеца» — задания с тем же условием внутри LMS.
Для трёх заданий единственным близнецом оказалась партия `pdf:*crylov*`, а она содержит
брак OCR: из 648 активных заданий у 222 в `accepted_answers` лежит обрывок латиницы
(`ae`, `oe`, `er`, `a`), а не ответ. Один такой обрывок и приехал: задание 3285 получило
ответ **`er`**.

Числовые ответы из той же партии (3058 → `42`, 3169 → `2809`) выглядят правдоподобно, но
источник один и тот же и доверия к нему до разбора [[tsk-367]] нет. Ошибочный «верный
ответ» хуже ручной проверки: ученик отвечает правильно и получает «неверно».

ЧТО ДЕЛАЕТ
Заменяет `solution_rules` у трёх заданий на правило с обязательной ручной проверкой
(без `accepted_answers`). Прежний ответ сохраняется в `task_content.answer_raw` с пометкой
источника — чтобы [[tsk-367]] мог сверить его со сборником, а не искать заново.

ИДЕМПОТЕНТНОСТЬ
UPDATE только по трём id и только если ответ там всё ещё тот, что ожидается.

Запуск: dry-run по умолчанию;
  python scripts/fix_crylov_twin_answers_tsk367.py
  DBCHECK_OK=1 python scripts/fix_crylov_twin_answers_tsk367.py --apply
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

from app.schemas.solution_rules import SolutionRules  # noqa: E402

# id → (ожидаемый сейчас ответ, близнец, откуда он)
TARGETS = {
    3285: ("er", 2685, "pdf:d4:pdf:crylov:v14:20260602:v14t16"),
    3058: ("42", 4560, "crylov:v11t9"),
    3169: ("2809", 2401, "pdf:d4:pdf:crylov:v1:20260602:v1t15"),
}


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
            rows = await conn.fetch(
                "SELECT id, max_score, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans "
                "FROM tasks WHERE id = ANY($1::int[])", sorted(TARGETS))
            for r in rows:
                expected = TARGETS[r["id"]][0]
                print(f"ДО: id={r['id']} ответ={r['ans']!r} (ожидали {expected!r})")
                if r["ans"] != expected:
                    raise AssertionError(
                        f"id={r['id']}: ответ {r['ans']!r} не тот, что ожидался — состояние изменилось, не трогаю")

            for tid, (ans, twin, twin_uid) in TARGETS.items():
                ms = next(r["max_score"] for r in rows if r["id"] == tid) or 1
                rules = SolutionRules(
                    max_score=ms, scoring_mode="all_or_nothing",
                    auto_check=True, manual_review_required=True,
                ).model_dump()
                note = f"{ans} (из партии Крылова, близнец {twin} {twin_uid} — требует сверки, tsk-367)"
                res = await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text), true) "
                    "WHERE id = $1 AND solution_rules#>>'{short_answer,accepted_answers,0,value}' = $4",
                    tid, json.dumps(rules), note, ans)
                if int(res.split()[-1]) != 1:
                    raise AssertionError(f"id={tid}: обновлено {res}")

            check = await conn.fetch(
                "SELECT id, (solution_rules->>'manual_review_required')::bool AS manual, "
                "jsonb_typeof(solution_rules->'short_answer') AS sa, "
                "task_content->>'answer_raw' AS raw FROM tasks WHERE id = ANY($1::int[])",
                sorted(TARGETS))
            for r in check:
                print(f"ПОСЛЕ: id={r['id']} ручная={r['manual']} short_answer={r['sa']} "
                      f"answer_raw={r['raw'][:45]}…")
                if not r["manual"] or r["sa"] != "null" or not r["raw"]:
                    raise AssertionError(f"id={r['id']}: состояние после записи неверное")

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
