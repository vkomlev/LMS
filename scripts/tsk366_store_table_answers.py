# -*- coding: utf-8 -*-
"""tsk-366 (грунт): сохранить извлечённые ТАБЛИЧНЫЕ ответы в `task_content.answer_raw`.

ЗАЧЕМ
Табличные ответы ЕГЭ (№25 — до 9 строк по 2 значения, №17/18/26 — пара значений) добывались
трижды ([[tsk-100]], [[tsk-361]], [[tsk-362]]), но нигде не сохранялись: в `accepted_answers`
их класть нельзя (одно поле ввода → задание «всегда неверно»), поэтому задания уходили в
ручную проверку, а сам ответ оставался только в файлах аудита рабочего дерева. Решение
оператора 2026-07-22: **хранить нужно**, формат — значения через пробельные символы.

ФОРМАТ (решение оператора)
Ячейки строки — через пробел, строки — через перевод строки:
    1237678 95206
    12300678 946206
    ...
Одиночная пара («44101521 48825239») — одна строка.

КУДА
`task_content.answer_raw` — тот же слот, из которого [[tsk-325]] строила правила проверки для
790 заданий. Правила проверки НЕ меняются: задания остаются с `manual_review_required=true`
до появления типа `TBL_COM` ([[tsk-366]]), который научится такой ответ принимать и сверять.
То есть это сохранение данных, а не изменение поведения.

ИДЕМПОТЕНТНОСТЬ / BLAST-RADIUS
Пишет только в ключ `answer_raw` и только тем заданиям, где его ещё нет (или он пуст).
Остальной `task_content` и `solution_rules` не трогаются. Обратимо: удалить ключ.

Запуск: dry-run по умолчанию;
  python scripts/tsk366_store_table_answers.py --answers <файл.json>
  DBCHECK_OK=1 python scripts/tsk366_store_table_answers.py --answers <файл.json> --apply
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

SELECT_TARGETS = """
SELECT id, course_id,
       task_content->>'type' AS task_type,
       task_content->>'answer_raw' AS answer_raw,
       (solution_rules->>'manual_review_required')::bool AS manual,
       left(regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g'), 90) AS stem
FROM tasks
WHERE id = ANY($1::int[]) AND is_active
ORDER BY id
"""

UPDATE_ONE = """
UPDATE tasks
SET task_content = jsonb_set(task_content, '{answer_raw}', to_jsonb($2::text), true)
WHERE id = $1
  AND is_active
  AND coalesce(task_content->>'answer_raw', '') = ''
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


async def main(answers_path: Path, apply: bool) -> None:
    data = json.loads(answers_path.read_text(encoding="utf-8"))
    table = {int(k): v for k, v in data["answers"].items()}
    print(f"Табличных ответов в файле: {len(table)}")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS, sorted(table))
            found = {r["id"]: r for r in rows}
            missing = sorted(set(table) - set(found))
            if missing:
                print(f"  Нет в БД / не активны: {len(missing)} → {missing[:10]}")

            already = [r["id"] for r in rows if (r["answer_raw"] or "").strip()]
            if already:
                print(f"  Уже с answer_raw (не трогаю): {len(already)} → {already[:10]}")

            not_manual = [r["id"] for r in rows if not r["manual"]]
            if not_manual:
                print(f"  ВНИМАНИЕ: без ручной проверки (ожидали manual=true): {not_manual[:10]}")

            print("\nПримеры записываемого:")
            for r in rows[:5]:
                v = table[r["id"]].replace("\n", " | ")
                print(f"  id={r['id']} курс={r['course_id']} [{r['task_type']}] "
                      f"«{r['stem'][:50]}…» → {v[:70]}")

            updated = 0
            for tid, value in table.items():
                if tid not in found:
                    continue
                res = await conn.execute(UPDATE_ONE, tid, value)
                updated += int(res.split()[-1])
            expected = len([r for r in rows if not (r["answer_raw"] or "").strip()])
            print(f"\nЗаписано answer_raw: {updated} (ожидали {expected})")
            if updated != expected:
                raise AssertionError(f"обновлено {updated}, ожидали {expected}")

            # ---- Верификация внутри транзакции, построчно (не агрегатом — урок tsk-317) ----
            check = await conn.fetch(
                "SELECT id, task_content->>'answer_raw' AS v FROM tasks WHERE id = ANY($1::int[])",
                sorted(found),
            )
            bad = [(r["id"], r["v"]) for r in check
                   if r["v"] != table[r["id"]] and r["id"] not in already]
            if bad:
                raise AssertionError(f"значение разошлось у {len(bad)}: {bad[:3]}")

            # Правила проверки не должны были измениться.
            changed_rules = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE id = ANY($1::int[]) "
                "AND (solution_rules->>'manual_review_required')::bool IS NOT TRUE",
                sorted(found),
            )
            print(f"Проверка: у всех целей ручная проверка на месте "
                  f"(без неё: {changed_rules})")

            print("\nOK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(Path(args.answers), args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
