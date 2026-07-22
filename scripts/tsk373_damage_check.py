# -*- coding: utf-8 -*-
"""tsk-373, шаг 3: проверить, не пострадали ли ученики от неверного ключа.

ЗАЧЕМ
Расхождение «ответ в LMS ≠ ключ источника» опасно не само по себе, а тем, что по неверному
ключу могли оценить живые попытки: ученик решает верно, а система ставит «неверно»
(так было у 3177 в [[tsk-369]]). Отчёт без этой проверки не отвечает на главный вопрос —
надо ли кому-то пересчитывать результат.

ЧТО СЧИТАЕТСЯ
  1. по заданиям из разбора tsk-373 — все попытки, с разбивкой по способу оценки
     (`manual_teacher` — отметка преподавателя, ответ ученика не хранится и ключ не
     применялся; `spw_web` / `learning_api` — автопроверка по ключу);
  2. независимая сплошная проверка по ВСЕМ заданиям kompege: попытки, отмеченные как
     неверные, у которых ответ ученика совпадает с ключом задачи источника. Это прямой
     признак ущерба и он не зависит от классификации условий.

Только чтение. На выходе JSON и печать по ученикам.

Запуск: python scripts/tsk373_damage_check.py --cache <кэш.json> --classify <файл.json>
                                              --out <файл.json>
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
from tsk370_scan import dsn  # noqa: E402
from tsk370_verify_source import kompege_id  # noqa: E402
from tsk373_classify import answers_loose_equal  # noqa: E402

AUTO = ("spw_web", "learning_api", "lms")


async def main(cache_path: Path, classify_path: Path, out: Path) -> None:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cls = json.loads(classify_path.read_text(encoding="utf-8"))
    watched = {m["id"] for m in cls["mismatch"]} | {m["id"] for m in cls["loose_ok"]} \
        | {m["id"] for m in cls["no_answer"]}

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        tasks = await conn.fetch(
            "SELECT id, external_uid, task_content->>'stem' AS stem, "
            "       task_content->>'source_kind' AS source_kind, "
            "       task_content->>'source_task_id' AS source_task_id "
            "FROM tasks WHERE is_active = true")
        results = await conn.fetch(
            "SELECT tr.id, tr.task_id, tr.user_id, u.full_name, u.email, "
            "       tr.is_correct, tr.score, tr.max_score, tr.source_system, "
            "       tr.answer_json->'response'->>'value' AS given, tr.submitted_at "
            "FROM task_results tr LEFT JOIN users u ON u.id = tr.user_id "
            "ORDER BY tr.submitted_at")
    finally:
        await conn.close()

    kid = {}
    for t in tasks:
        k = kompege_id(t["external_uid"], t["source_kind"],
                       t["source_task_id"], t["stem"])
        if k:
            kid[t["id"]] = k

    on_watched, graded_wrong_but_right = [], []
    for r in results:
        row = {"result_id": r["id"], "task_id": r["task_id"], "user_id": r["user_id"],
               "user": r["full_name"] or r["email"], "is_correct": r["is_correct"],
               "score": r["score"], "source_system": r["source_system"],
               "given": r["given"], "submitted_at": str(r["submitted_at"])[:19]}
        if r["task_id"] in watched:
            on_watched.append(row)
        k = kid.get(r["task_id"])
        if (k and r["source_system"] in AUTO and r["is_correct"] is False
                and r["given"] and isinstance(cache.get(k), dict)
                and answers_loose_equal(r["given"], cache[k].get("key"))):
            graded_wrong_but_right.append({**row, "kompege_id": k,
                                           "key_src": cache[k].get("key")})

    auto_watched = [r for r in on_watched if r["source_system"] in AUTO]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "watched_tasks": len(watched),
        "results_on_watched": on_watched,
        "auto_graded_on_watched": auto_watched,
        "graded_wrong_but_matches_source": graded_wrong_but_right,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"заданий под наблюдением (расхождение/без ответа): {len(watched)}")
    print(f"попыток по ним всего: {len(on_watched)}, "
          f"из них автопроверкой по ключу: {len(auto_watched)}")
    by_sys: dict[str, int] = {}
    for r in on_watched:
        by_sys[r["source_system"]] = by_sys.get(r["source_system"], 0) + 1
    for s, n in sorted(by_sys.items(), key=lambda x: -x[1]):
        print(f"    {s}: {n}")
    print("сплошная проверка по всем заданиям kompege — «отмечено неверно, а ответ "
          f"совпадает с ключом источника»: {len(graded_wrong_but_right)}")
    for r in graded_wrong_but_right:
        print(f"    задание {r['task_id']} (kompege {r['kompege_id']}), ученик "
              f"{r['user']} ({r['user_id']}), {r['submitted_at']}: "
              f"ответ {r['given']!r} против ключа {r['key_src']!r}")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--classify", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    asyncio.run(main(a.cache, a.classify, a.out))
