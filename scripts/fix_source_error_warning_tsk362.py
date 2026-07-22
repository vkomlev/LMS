# -*- coding: utf-8 -*-
"""tsk-362: снять авто-ответ там, где сам автор поста предупредил об ошибке источника.

ПОЧЕМУ
Разбирая остаток, нашлись задания, в тексте которых автор канала прямо пишет, что **ответ
на сайте-источнике неверный**:
  * 3119 — «‼️ Внимание, предварительно есть ошибка в ответе!» (kompege:27615, записали 736);
  * 3343 / 2937 — «Внимание, в ответе на сайте ошибка. Ошибка идёт от решения Кабанова, где
    он округляет вверх, где нужно округлять вниз» (polyakov:7926, записали 33).

Основной проход [[tsk-362]] сверял, что по ID лежит ТА ЖЕ задача, но не читал предупреждений
о качестве ответа. Формально ответ источника — верный ключ; по существу автор курса с ним
не согласен. Ошибочный авто-ответ хуже ручной проверки: ученик решает правильно и получает
«неверно», причём молча.

ЧТО ДЕЛАЕТ
Возвращает эти задания на обязательную ручную проверку, сохраняя ответ источника в
`task_content.answer_raw` с пометкой — чтобы методист решал, а не искал заново.

Не трогает задания, где слово «ошибка» относится к ходу решения в видеоразборе
(«долго искал ошибку», «ищем ошибки и исправляем») — там ответ источника под сомнение
не ставится.

Запуск: dry-run по умолчанию;
  python scripts/fix_source_error_warning_tsk362.py
  DBCHECK_OK=1 python scripts/fix_source_error_warning_tsk362.py --apply
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

# id → (ожидаемый сейчас ответ, источник, чем предупредил автор)
TARGETS = {
    3119: ("736", "kompege:27615",
           "автор поста: «предварительно есть ошибка в ответе»"),
    2937: ("33", "polyakov:7926",
           "автор поста 3343 о том же задании: «в ответе на сайте ошибка» (округление вверх вместо вниз)"),
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
                    raise AssertionError(f"id={r['id']}: ответ {r['ans']!r} не тот — состояние изменилось")

            for tid, (ans, src, why) in TARGETS.items():
                ms = next(r["max_score"] for r in rows if r["id"] == tid) or 1
                rules = SolutionRules(max_score=ms, scoring_mode="all_or_nothing",
                                      auto_check=True, manual_review_required=True).model_dump()
                note = f"{ans} (ответ источника {src}; {why} — нужна проверка методиста, tsk-362)"
                res = await conn.execute(
                    "UPDATE tasks SET solution_rules = $2::jsonb, "
                    "task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($3::text), true) "
                    "WHERE id = $1 AND solution_rules#>>'{short_answer,accepted_answers,0,value}' = $4",
                    tid, json.dumps(rules), note, ans)
                if int(res.split()[-1]) != 1:
                    raise AssertionError(f"id={tid}: обновлено {res}")

            check = await conn.fetch(
                "SELECT id, (solution_rules->>'manual_review_required')::bool AS manual, "
                "jsonb_typeof(solution_rules->'short_answer') AS sa, task_content->>'answer_raw' AS raw "
                "FROM tasks WHERE id = ANY($1::int[])", sorted(TARGETS))
            for r in check:
                print(f"ПОСЛЕ: id={r['id']} ручная={r['manual']} short_answer={r['sa']} "
                      f"answer_raw={r['raw'][:60]}…")
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
