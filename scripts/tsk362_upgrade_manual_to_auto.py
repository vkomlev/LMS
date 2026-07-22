# -*- coding: utf-8 -*-
"""tsk-362, добор: перевести задания из ручной проверки в авто, когда ответ всё-таки нашёлся.

ЗАЧЕМ
Основной проход [[tsk-362]] честно отправил в ручную проверку всё, для чего источник не
нашёлся. Часть таких заданий добралась позже, двумя путями:
  * **перебор источника** — в шапке был числовой ID, но не было имени источника
    («Задание 13_23749 Демоверсия 2026»); ID прогнан по kompege/sdamgia/kpolyakov, и только
    у одного из них условие совпало (`tsk362_probe_unknown_source.py`);
  * **позиция в подборке Яндекса** — `ext:calib:yandex:…:<подборка>:<позиция>`; подборка
    открывается тем же `public_get_variant_request_item`, что и вариант, и отдаёт все 27
    задач с ответами. Разрешённые так UUID совпали с теми, что в [[tsk-361]] были получены
    совсем другим способом (через поле `task_id` в событии Метрики) — независимое
    подтверждение обоих методов.

Гейт сверки тот же: дословный фрагмент условия + значимые числа. Записываются только `match`.

ГАРАНТИИ
UPDATE только по заданиям, которые СЕЙЧАС на обязательной ручной проверке и без
`accepted_answers` — то есть скрипт не может перезаписать ни авто-ответ, ни чужую правку.
Прежнее правило сохраняется в файл бэкапа.

Запуск: dry-run по умолчанию;
  python scripts/tsk362_upgrade_manual_to_auto.py --answers upgrade.json --backup b.json
  DBCHECK_OK=1 python scripts/tsk362_upgrade_manual_to_auto.py --answers upgrade.json --backup b.json --apply
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

GUARD = """
  AND is_active
  AND (solution_rules->>'manual_review_required')::bool IS TRUE
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) = 0
"""

SELECT_TARGETS = f"""
SELECT id, course_id, max_score, solution_rules,
       left(regexp_replace(task_content->>'stem', '<[^>]+>', ' ', 'g'), 80) AS stem
FROM tasks WHERE id = ANY($1::int[]) {GUARD} ORDER BY id
"""

UPDATE_ONE = f"UPDATE tasks SET solution_rules = $2::jsonb WHERE id = $1 {GUARD}"


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


async def main(answers_path: Path, backup_path: Path, apply: bool) -> None:
    data = json.loads(answers_path.read_text(encoding="utf-8"))
    answers = {int(k): v for k, v in data["answers"].items()}
    origin = data.get("origin", {})
    print(f"К переводу в авто: {len(answers)}")

    conn = await asyncpg.connect(_dsn())
    try:
        async with conn.transaction():
            rows = await conn.fetch(SELECT_TARGETS, sorted(answers))
            found = {r["id"]: r for r in rows}
            skipped = sorted(set(answers) - set(found))
            if skipped:
                print(f"  Пропущены (уже не на ручной проверке / есть ответ): {skipped}")

            backup_path.write_text(json.dumps(
                {str(r["id"]): json.loads(r["solution_rules"]) for r in rows},
                ensure_ascii=False, indent=1), encoding="utf-8")

            for r in rows:
                print(f"  id={r['id']} курс={r['course_id']} «{r['stem'][:46]}…» → "
                      f"{answers[r['id']]!r}  [{origin.get(str(r['id']), '')}]")

            for tid, row in found.items():
                ms = row["max_score"] or 1
                rules = SolutionRules(
                    max_score=ms, scoring_mode="all_or_nothing", auto_check=True,
                    manual_review_required=False,
                    short_answer=ShortAnswerRules(
                        normalization=["trim", "lower"],
                        accepted_answers=[ShortAnswerAccepted(value=answers[tid], score=ms)],
                    ),
                ).model_dump()
                res = await conn.execute(UPDATE_ONE, tid, json.dumps(rules))
                if int(res.split()[-1]) != 1:
                    raise AssertionError(f"id={tid}: обновлено {res}")

            # ---- Верификация построчно (не агрегатом — урок tsk-317) ----
            check = await conn.fetch(
                "SELECT id, solution_rules#>>'{short_answer,accepted_answers,0,value}' AS v, "
                "(solution_rules->>'manual_review_required')::bool AS manual, "
                "(solution_rules->>'max_score')::int AS sr_ms, max_score "
                "FROM tasks WHERE id = ANY($1::int[])", sorted(found))
            for r in check:
                if r["v"] != answers[r["id"]] or r["manual"] or r["sr_ms"] != r["max_score"]:
                    raise AssertionError(f"id={r['id']}: v={r['v']!r} manual={r['manual']} "
                                         f"ms={r['sr_ms']}/{r['max_score']}")
            print(f"\nПереведено в авто: {len(found)}, все значения совпали с планом.")

            print("OK: все проверки пройдены.")
            if not apply:
                raise RuntimeError("DRY-RUN: откатываю (запусти с --apply при DBCHECK_OK=1)")
        print("\nЗАПИСАНО И ЗАКОММИЧЕНО.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        asyncio.run(main(Path(args.answers), Path(args.backup), args.apply))
    except RuntimeError as exc:
        print(f"\n{exc}")
        sys.exit(0 if "DRY-RUN" in str(exc) else 1)
