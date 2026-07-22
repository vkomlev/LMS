# -*- coding: utf-8 -*-
"""tsk-366: пометить задания с табличным ответом флагом `pending_tbl_com`.

ЗАЧЕМ (решение оператора 2026-07-22)
Как только в SPW появится поле ввода под таблицу и тип `TBL_COM`, эти задания надо перевести
на автопроверку. Чтобы их не искать заново и не полагаться на память, пометка ставится прямо
в данных: `task_content.pending_tbl_com = true`. Это и метка «ждёт TBL_COM», и готовый фильтр
для миграции.

КОГО ПОМЕЧАЕМ
Активные задания, чей верный ответ — **два и более числа**, разделённых пробельными символами
(формат хранения, выбранный оператором). Ответ берётся из `accepted_answers`, а если его там
нет — из `task_content.answer_raw`. Два состояния, обе нужны:
  * **ответ в `accepted_answers`** — задание уже на автопроверке, но ученик обязан угадать
    формат разделителей: одно поле, а значений несколько. Работает вслепую;
  * **ответ в `answer_raw`** — задание на ручной проверке, ответ сохранён впрок
    ([[tsk-100]], [[tsk-361]], [[tsk-362]]).

Не помечаем: ответы-«рисунки» (вывод программы со звёздочками и переносами), текстовые ответы
с пробелом («Три встречи»), код. Критерий строгий: все токены — числа.

ИДЕМПОТЕНТНОСТЬ
Ставит ключ только там, где его ещё нет. Ничего, кроме этого ключа, не трогает: ни правила
проверки, ни активность. Снять — удалить ключ.

Запуск: dry-run по умолчанию;
  python scripts/tsk366_mark_pending_tbl_com.py
  DBCHECK_OK=1 python scripts/tsk366_mark_pending_tbl_com.py --apply
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

# Табличный ответ = 2+ числа через пробел/перевод строки.
SELECT_TARGETS = """
SELECT id, course_id, external_uid,
       coalesce(solution_rules#>>'{short_answer,accepted_answers,0,value}',
                task_content->>'answer_raw') AS ans,
       (solution_rules#>>'{short_answer,accepted_answers,0,value}') IS NOT NULL AS in_auto,
       task_content ? 'pending_tbl_com' AS already
FROM tasks
WHERE is_active
  AND coalesce(solution_rules#>>'{short_answer,accepted_answers,0,value}',
               task_content->>'answer_raw') ~ '^\\d+([ \\n\\r\\t]+\\d+)+$'
ORDER BY id
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = jsonb_set(task_content, '{pending_tbl_com}', 'true'::jsonb, true)
WHERE id = $1 AND NOT (task_content ? 'pending_tbl_com')
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
            rows = await conn.fetch(SELECT_TARGETS)
            in_auto = [r for r in rows if r["in_auto"]]
            in_raw = [r for r in rows if not r["in_auto"]]
            print(f"Заданий с табличным ответом: {len(rows)}")
            print(f"  на автопроверке (ученик угадывает формат): {len(in_auto)}")
            print(f"  на ручной проверке, ответ сохранён впрок:  {len(in_raw)}")
            fam: dict[str, int] = {}
            for r in rows:
                key = (r["external_uid"] or "?").split(":")[0]
                fam[key] = fam.get(key, 0) + 1
            print(f"  по семействам: {fam}")
            print("\nПримеры:")
            for r in rows[:5]:
                print(f"  id={r['id']} [{r['external_uid']}] → {r['ans'][:60]!r}")

            updated = 0
            for r in rows:
                res = await conn.execute(UPDATE_ONE, r["id"])
                updated += int(res.split()[-1])
            print(f"\nПомечено: {updated} (уже были помечены: {len(rows) - updated})")

            check = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (task_content->>'pending_tbl_com')::bool IS TRUE", [r["id"] for r in rows])
            if check != len(rows):
                raise AssertionError(f"пометка стоит у {check} из {len(rows)}")

            # Ничего лишнего: флаг не должен появиться за пределами выборки.
            outside = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE task_content ? 'pending_tbl_com' "
                "AND id <> ALL($1::int[])", [r["id"] for r in rows])
            if outside:
                raise AssertionError(f"флаг стоит у {outside} заданий вне выборки")

            print("OK: все проверки пройдены.")
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
