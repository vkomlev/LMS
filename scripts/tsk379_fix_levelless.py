# -*- coding: utf-8 -*-
"""tsk-379, шаг 4 (доп.): срез шапки crylov БЕЗ строки «Уровень …».

ЗАЧЕМ ОТДЕЛЬНЫМ ШАГОМ
`tsk379_fix_stems.py` сознательно обошёл 15 заданий `crylov`, где шапка — только
«Задание N Сборник Крылова С.С. вариант M.» без слова «Уровень»: постановка tsk-379
описывала лишь шапку с уровнем, и до явного решения оператора резать её было нельзя.
Оператор посмотрел список (все 15 — с примером и первоисточником; у 10 уровень уже
подтверждён каноном 1 из поста @cyberguru_ege, у 5 — канон 2, ручной вердикт tsk-381/
tsk-382) и разрешил: «Да, такие шапки тоже можно убрать» (2026-07-25). Отсутствие
слова «Уровень» здесь не значит отсутствие канона сложности — сам факт срезаемой
шапки к канону в `difficulty_id` отношения не имеет, он уже установлен отдельно.

Резать регуляркой из `tsk379_scan.cut_header` (тот же код, что и в шаге 2) — только
пары `WHOLE/INLINE_HEADER_NOLEVEL_RE`, регулярки с «Уровень» здесь бить не должны
(если сработают — задание уже входило в план шага 2, RuntimeError ниже это ловит).

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Бэкап → транзакция → построчная
проверка после COMMIT (урок [[tsk-317]]).

Запуск: python scripts/tsk379_fix_levelless.py --backup <файл.json> [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk370_scan import ASK_RE, dsn, strip_html  # noqa: E402
from tsk379_scan import LEVEL_WORD, LEVELLESS_LEADER, cut_header  # noqa: E402

LEVEL_ANYWHERE_RE = re.compile(r"Уровень\s+" + LEVEL_WORD, re.IGNORECASE)
LEVELLESS_CANDIDATE_RE = re.compile(LEVELLESS_LEADER, re.IGNORECASE)


def build_plan(rows: dict[int, asyncpg.Record]) -> dict[int, str]:
    """Карта id → новое условие для заданий с шапкой crylov без «Уровень …»."""
    plan: dict[int, str] = {}
    for tid, r in rows.items():
        stem = r["stem"] or ""
        if LEVEL_ANYWHERE_RE.search(stem):
            raise RuntimeError(
                f"{tid}: в стеме есть «Уровень …» — это задание из шага 2 "
                f"(tsk379_fix_stems.py), не из levelless-класса")

        new, removed = cut_header(stem)
        if not removed:
            raise RuntimeError(f"{tid}: шапка не найдена — условие в базе изменилось")
        if new == stem:
            raise RuntimeError(f"{tid}: правка не собралась")

        left = strip_html(new)
        if not left:
            raise RuntimeError(f"{tid}: после правки условие осталось без текста")
        if not ASK_RE.search(left):
            raise RuntimeError(f"{tid}: после правки в условии нет постановки задачи")
        if LEVELLESS_CANDIDATE_RE.search(new):
            raise RuntimeError(f"{tid}: после правки шапка осталась")
        plan[tid] = new
    return plan


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, task_content->>'stem' AS stem "
            "FROM tasks WHERE is_active AND split_part(external_uid, ':', 1) = 'crylov' "
            "  AND task_content->>'stem' ~* $1"
            "  AND task_content->>'stem' !~* 'Уровень\\s+"
            "(простой|лёгкий|легкий|средний|сложный)'",
            LEVELLESS_LEADER)}

        plan = build_plan(rows)
        ids = sorted(plan)

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in ids], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений: {backup_path} ({len(ids)} заданий)")
        print(f"Найдено: {sorted(ids)}\n")

        cut = sum(len(rows[i]["stem"]) - len(plan[i]) for i in ids)
        print(f"Всего заданий: {len(ids)}; срезано {cut} символов разметки")

        print("\nПримеры до/после (начало условия):")
        for tid in ids:
            was = strip_html(rows[tid]["stem"])
            now = strip_html(plan[tid])
            print(f"\n[{tid}] {rows[tid]['external_uid']}")
            print(f"  было:  {was[:160]}")
            print(f"  стало: {now[:160]}")

        if not apply:
            print("\nDRY-RUN: в базу ничего не записано. Повторить с --apply.")
            return

        async with conn.transaction():
            for tid in ids:
                await conn.execute(
                    "UPDATE tasks SET task_content = jsonb_set("
                    "  task_content, '{stem}', to_jsonb($2::text), true) "
                    "WHERE id = $1", tid, plan[tid])
        print("\nCOMMIT выполнен. Построчная проверка после записи:")

        check = {r["id"]: r["stem"] for r in await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks WHERE id = ANY($1::int[])",
            ids)}
        bad = [i for i in ids if check.get(i) != plan[i]]
        print(f"  сверено заданий: {len(ids)}; совпало: {len(ids) - len(bad)}")
        if bad:
            raise RuntimeError(f"после COMMIT не совпало: {bad}")
        left = await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE is_active AND id = ANY($1::int[]) "
            "  AND task_content->>'stem' ~* $2", ids, LEVELLESS_LEADER)
        print(f"  осталось заданий из плана с шапкой: {left}")
        if left:
            raise RuntimeError("шапка осталась у части заданий — разбирать руками")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.backup, args.apply))
