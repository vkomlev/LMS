# -*- coding: utf-8 -*-
"""tsk-412 follow-up: пометить 10 turtle_sim-заданий курса 165 (id 10029-10038)
как многострочный ответ (`task_content.multiline_answer=true`).

ПОЧЕМУ
Живой прогон на проде (2026-08-05) обнаружил: поле «Ответ» типа SA рендерится
клиентом SPW как однострочный `<Input type="text">` (`components/task/
TaskFormSA.tsx`). Браузер удаляет переводы строк из value однострочных text-
контролов (WHATWG value sanitization algorithm) ДО того, как что-либо уходит на
сервер — многострочная Python-программа доезжает разбитой в одну строку и не
проходит `ast.parse`. Подтверждено на проде: `task_results.answer_json` для
task_id=10029 содержал ответ без единого `\\n`, хотя форма заполнялась
многострочным кодом.

Фикс — двусторонний: `app/schemas/task_content.py` получил флаг
`multiline_answer`, SPW `TaskFormSA.tsx` получил проп `multiline` (рендерит
`<textarea>` вместо `<Input>`, когда true). Этот скрипт — вторая половина:
проставить флаг в уже вставленных 10 заданиях (materials_id=314→tasks
10029-10038, см. tsk412_import_turtle_tasks.py).

Запуск: dry-run по умолчанию (ничего не пишет, всегда ROLLBACK);
  PYTHONPATH=. python scripts/tsk412_set_multiline_answer.py
  PYTHONPATH=. DBCHECK_OK=1 python scripts/tsk412_set_multiline_answer.py --apply
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

TASK_IDS = list(range(10029, 10039))  # 10029..10038 включительно


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
                "SELECT id, course_id, task_content FROM tasks WHERE id = ANY($1::int[]) ORDER BY id",
                TASK_IDS,
            )
            if len(rows) != len(TASK_IDS):
                raise AssertionError(f"нашлось {len(rows)} из {len(TASK_IDS)}")

            print("=" * 78)
            print(f"tsk-412 · multiline_answer=true для {len(TASK_IDS)} заданий · "
                  f"{'ПРИМЕНЕНИЕ' if apply else 'DRY-RUN'}")
            print("=" * 78)

            updates = []
            for r in rows:
                if r["course_id"] != 165:
                    raise AssertionError(f"id={r['id']} course_id={r['course_id']} != 165 — стоп")
                content = json.loads(r["task_content"]) if isinstance(r["task_content"], str) else dict(r["task_content"])
                if content.get("type") != "SA":
                    raise AssertionError(f"id={r['id']} type={content.get('type')} != SA — стоп")
                already = content.get("multiline_answer") is True
                new_content = dict(content)
                new_content["multiline_answer"] = True
                updates.append((r["id"], new_content, already))
                print(f"id={r['id']:>5} было={content.get('multiline_answer')!r} "
                      f"{'(уже true, пропуск)' if already else '→ true'}")

            if not apply:
                print("\nDRY-RUN: ничего не записано. Повтор с --apply.")
                return

            for task_id, new_content, already in updates:
                if already:
                    continue
                await conn.execute(
                    "UPDATE tasks SET task_content = $1::jsonb WHERE id = $2",
                    json.dumps(new_content, ensure_ascii=False), task_id,
                )

            after = await conn.fetch(
                "SELECT id, (task_content->>'multiline_answer')::boolean AS flag "
                "FROM tasks WHERE id = ANY($1::int[])",
                TASK_IDS,
            )
            bad = [a["id"] for a in after if a["flag"] is not True]
            print("\n=== ПРОВЕРКА ПОСЛЕ UPDATE ===")
            for a in after:
                print(f"id={a['id']:>5} multiline_answer={a['flag']}")
            if bad:
                raise AssertionError(f"не проставилось у {bad} — откатываю всё")

            print(f"\nОбновлено заданий: {sum(1 for _, _, already in updates if not already)}")
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
    except AssertionError as exc:
        print(f"\nОШИБКА ПРОВЕРКИ: {exc}")
        sys.exit(1)
