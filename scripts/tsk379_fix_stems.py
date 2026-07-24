# -*- coding: utf-8 -*-
"""tsk-379, шаг 2: убрать из условий служебную шапку импорта («Уровень …» / «Решение
задания NNNN»).

ЧТО И ПОЧЕМУ
Разбор `scripts/tsk379_scan.py` дал 170 активных заданий (84 `tg:ege`, 86 `crylov`, из
них одно — 3156 — одновременно и «Решение задания», и «Уровень») с шапкой вида
«Задание 24_24613 КЕГЭ. Уровень сложный.» перед условием. Уровень сложности дублирует
`difficulty_id` (канон подтверждён по обеим партиям: [[tsk-381]], [[tsk-382]] закрыты
2026-07-23 — у всех 112 активных на тот момент заданий Крылова есть обоснование канон 1
или 2, ограничение «не резать 12 без канона» из исходной постановки [[tsk-379]] снято).
«Решение задания N» вводит в заблуждение: дальше идёт условие, а не решение.

ВНЕ ОХВАТА ЭТОЙ ПРАВКИ: 15 заданий `crylov` с шапкой БЕЗ строки «Уровень …» (только
«Задание N Сборник Крылова С.С. вариант M.», без уровня — те самые 9525/9528/9530 и
похожие из [[tsk-382]], где в тексте условия уровня никогда не было). Это отдельный,
не описанный в постановке tsk-379 класс — решение по нему не принято, скрипт их не
трогает. Список — `unmatched_levelless` в отчёте scan.

Резать можно ровно шапку — «Задание/Решение задания … Уровень X» (плюс один соседний
`<br>`, если шапка делит параграф с реальным текстом), а не «от маркера до конца»:
у части заданий (`tg:ege:961` и подобные) в том же параграфе после шапки идёт
комментарий оператора и сразу реальный вопрос — слепой срез отрезал бы часть условия
(урок [[tsk-370]]).

dry-run по умолчанию; `--apply` при DBCHECK_OK=1. Перед записью каждое условие
сверяется с тем, на котором собиралась правка. После COMMIT — независимая проверка
ПОСТРОЧНО по всему затронутому множеству (урок [[tsk-317]]).

Запуск: python scripts/tsk379_fix_stems.py --backup <файл.json> [--apply]
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
from tsk379_scan import LEVEL_WORD, cut_header  # noqa: E402

LEVEL_IN_REMOVED_RE = re.compile(r"Уровень\s+" + LEVEL_WORD, re.IGNORECASE)


def build_plan(rows: dict[int, asyncpg.Record]) -> tuple[dict[int, str], list[int]]:
    """Карта id → новое условие для заданий с шапкой, где есть строка «Уровень …».

    Заданий без «Уровень …» (только источник/сборник) в план не попадают — отдельный
    неразобранный класс, второй элемент возврата — их id для отчёта.
    """
    plan: dict[int, str] = {}
    levelless: list[int] = []
    for tid, r in rows.items():
        stem = r["stem"] or ""
        new, removed = cut_header(stem)
        if not removed:
            raise RuntimeError(f"{tid}: шапка не найдена — условие в базе изменилось")
        if not LEVEL_IN_REMOVED_RE.search("".join(removed)):
            levelless.append(tid)
            continue
        if new == stem:
            raise RuntimeError(f"{tid}: правка не собралась")

        left = strip_html(new)
        if not left:
            raise RuntimeError(f"{tid}: после правки условие осталось без текста")
        if not ASK_RE.search(left):
            raise RuntimeError(f"{tid}: после правки в условии нет постановки задачи")
        if LEVEL_IN_REMOVED_RE.search(new):
            raise RuntimeError(f"{tid}: после правки осталась строка «Уровень …»")
        plan[tid] = new
    return plan, levelless


async def main(backup_path: Path, apply: bool) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = {r["id"]: r for r in await conn.fetch(
            "SELECT id, external_uid, is_active, task_content->>'stem' AS stem "
            "FROM tasks WHERE is_active AND ("
            "     split_part(external_uid, ':', 1) IN ('crylov', 'tg')"
            "  OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d'"
            ") AND (task_content->>'stem' ~* 'Уровень\\s+"
            "(простой|лёгкий|легкий|средний|сложный)'"
            "  OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d')")}

        plan, levelless = build_plan(rows)
        ids = sorted(plan)

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(json.dumps(
            [{"id": i, "external_uid": rows[i]["external_uid"], "stem": rows[i]["stem"]}
             for i in ids], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Бэкап прежних значений: {backup_path} ({len(ids)} заданий)")
        print(f"Вне охвата (шапка без «Уровень …», {len(levelless)}): {sorted(levelless)}\n")

        by_src: dict[str, int] = {}
        for tid in ids:
            src = rows[tid]["external_uid"].split(":")[0]
            by_src[src] = by_src.get(src, 0) + 1
        for src, n in sorted(by_src.items(), key=lambda kv: -kv[1]):
            print(f"  {src}: {n}")
        cut = sum(len(rows[i]["stem"]) - len(plan[i]) for i in ids)
        print(f"\nВсего заданий: {len(ids)}; срезано {cut} символов разметки")

        print("\nПримеры до/после (начало условия):")
        for tid in ([3156] + ids[:2] + ids[-2:]):
            if tid not in plan:
                continue
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
            "SELECT count(*) FROM tasks WHERE is_active AND id = ANY($1::int[]) AND ("
            "     task_content->>'stem' ~* 'Уровень\\s+"
            "(простой|лёгкий|легкий|средний|сложный)'"
            "  OR task_content->>'stem' ~* 'Решение\\s+задания\\s+\\d')", ids)
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
