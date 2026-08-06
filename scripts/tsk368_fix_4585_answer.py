# -*- coding: utf-8 -*-
"""tsk-368: записать перерешанный ответ для задания 4585 (Крылов в11 з26, квадраты на диагонали).

Источник дал спорный ответ «29 49» (автор поста 814 пишет «в ответе ошибка»); в БД до этого
скрипта стояло «29 69» (прежняя неточная оценка). Перерешано заново (максимум невзаимно
перекрывающихся квадратов = 29, максимально возможная длительность разброса абсцисс = 1477 —
см. reviews/2026-08-06-tsk368-*.md), решение подтверждено оператором 2026-08-06.

Dry-run по умолчанию, `--apply` при DBCHECK_OK=1, построчная проверка после COMMIT.

Запуск:
  python scripts/tsk368_fix_4585_answer.py --backup docs/qa/tsk368_4585_backup.json [--apply]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk369_collect import dsn  # noqa: E402

TASK_ID = 4585
NEW_VALUE = "29 1477"


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        row = await conn.fetchrow(
            "SELECT id, external_uid, is_active, solution_rules, "
            "       task_content->>'answer_raw' AS answer_raw "
            "FROM tasks WHERE id = $1", TASK_ID)
        if row is None:
            raise RuntimeError(f"id={TASK_ID} не найден")
        if not row["is_active"]:
            raise RuntimeError(f"id={TASK_ID} неактивен — ожидал is_active=true")

        sr = json.loads(row["solution_rules"] or "{}")
        old = ((sr.get("short_answer") or {}).get("accepted_answers") or [{}])[0].get("value")

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            {"id": TASK_ID, "external_uid": row["external_uid"],
             "solution_rules": sr, "answer_raw": row["answer_raw"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежнего правила: {backup_path}")
        print(f"  id={TASK_ID} {row['external_uid']}: {old!r} -> {NEW_VALUE!r}")

        async with conn.transaction():
            new_sr = jsonb_set_value(sr, NEW_VALUE)
            await conn.execute(
                "UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1",
                TASK_ID, json.dumps(new_sr, ensure_ascii=False))

            check = await conn.fetchval(
                "SELECT solution_rules#>>'{short_answer,accepted_answers,0,value}' "
                "FROM tasks WHERE id = $1", TASK_ID)
            if check != NEW_VALUE:
                raise AssertionError(f"проверка внутри транзакции не прошла: {check!r}")
            print("Внутри транзакции: обновлено и проверено.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")

        print("\nЗАПИСАНО И ЗАКОММИЧЕНО. Независимая проверка после COMMIT:")
        after = await conn.fetchrow(
            "SELECT solution_rules#>>'{short_answer,accepted_answers,0,value}' AS ans, "
            "       (solution_rules->>'manual_review_required')::bool AS manual "
            "FROM tasks WHERE id = $1", TASK_ID)
        print(f"  id={TASK_ID}: ответ={after['ans']!r} ручная_проверка={after['manual']}")
        if after["ans"] != NEW_VALUE:
            print("  ПРОБЛЕМА: значение после коммита не совпадает с ожидаемым")
            sys.exit(1)
    finally:
        await conn.close()


def jsonb_set_value(sr: dict, value: str) -> dict:
    sr = dict(sr)
    sa = dict(sr.get("short_answer") or {})
    sa["accepted_answers"] = [{"score": sr.get("max_score", 1), "value": value}]
    sr["short_answer"] = sa
    return sr


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(Path(a.backup), a.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
