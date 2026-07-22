# -*- coding: utf-8 -*-
"""tsk-362, шаг 2b: найти ответ у «близнеца» — задания с тем же условием внутри LMS.

ЗАЧЕМ
Посты Telegram — разборы задач, которые в большинстве своём уже лежат в LMS из других
партий (`wp_nav:`, `ext:`, `pdf:crylov`, `oge:`) и там ответ есть. Это второй, полностью
внутренний источник: не зависит ни от сайтов, ни от авторизации.

КАК СВЕРЯЕТСЯ (тот же гейт, что при загрузке с сайта)
Кандидат считается тем же заданием, только если совпали ДВА независимых признака:
  1. дословный фрагмент 60 символов из середины «буквенной» части условия;
  2. все «значимые» числа (3+ цифр) кандидата присутствуют в условии проверяемого задания.
Одного текста мало: задачи одного типа делят преамбулу дословно, различает их начинка
(обжиг [[tsk-354]] — нечёткое сравнение дало две ложные пары).

Если у нескольких близнецов ответы РАЗНЫЕ — задание помечается `conflict` и не пишется.

Ничего не пишет в БД: на выходе JSON для шага 3.

Запуск:  python scripts/tsk362_match_twins.py --items <items.json> --out <twins.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]

ANSWERED_SQL = """
SELECT id, course_id, external_uid,
       task_content->>'stem' AS stem,
       solution_rules#>'{short_answer,accepted_answers}' AS accepted
FROM tasks
WHERE is_active
  AND coalesce(jsonb_array_length(solution_rules#>'{short_answer,accepted_answers}'), 0) > 0
"""


def _dsn(server: str) -> str:
    for candidate in (project_root / ".mcp.json", Path(r"D:\Work\LMS\.mcp.json")):
        if not candidate.exists():
            continue
        cfg = json.loads(candidate.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", cfg)
        for arg in servers[server]["args"]:
            if isinstance(arg, str) and arg.startswith("postgresql://"):
                return arg
    raise RuntimeError(f"не нашёл DSN для {server}")


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def prose(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^а-яa-z]+", " ", s).strip()


def numbers(s: str) -> set:
    return set(re.findall(r"\d+", s or ""))


def key_numbers(s: str) -> set:
    return {n for n in numbers(s) if len(n) >= 3}


def shingles(p: str, size: int = 60, step: int = 30) -> list:
    """Куски по 60 букв с перекрытием — индекс для поиска «того же условия»."""
    if len(p) <= size:
        return [p] if p else []
    return [p[i:i + size] for i in range(0, len(p) - size + 1, step)]


async def main(items_path: Path, out_path: Path) -> None:
    items = json.loads(items_path.read_text(encoding="utf-8"))
    conn = await asyncpg.connect(_dsn("learn_prod_db"))
    try:
        answered = await conn.fetch(ANSWERED_SQL)
    finally:
        await conn.close()

    index = defaultdict(list)
    cand_by_id = {}
    for r in answered:
        txt = strip_html(r["stem"])
        p = prose(txt)
        if len(p) < 60:
            continue
        cand_by_id[r["id"]] = {
            "id": r["id"], "course_id": r["course_id"], "external_uid": r["external_uid"],
            "prose": p, "key_numbers": key_numbers(txt), "numbers": numbers(txt),
            "accepted": [a.get("value") for a in json.loads(r["accepted"])],
        }
        for sh in shingles(p):
            index[sh].append(r["id"])
    print(f"Проиндексировано заданий с ответом: {len(cand_by_id)}")

    results = []
    stats = defaultdict(int)
    for it in items:
        p = prose(it["stem"])
        my_nums = numbers(it["stem"])
        hits = set()
        for sh in shingles(p):
            hits.update(index.get(sh, []))
        # фрагмент кандидата внутри нашего условия — вторая сторона сравнения
        for cid, c in cand_by_id.items():
            if cid in hits:
                continue
            mid = c["prose"][max(0, len(c["prose"]) // 2 - 30):][:60]
            if mid and mid in p:
                hits.add(cid)

        confirmed = []
        for cid in hits:
            c = cand_by_id[cid]
            # Без единого «значимого» числа числовой признак пустой, и остаётся только
            # текст — а он у задач одного типа общий дословно. Такой близнец не считается:
            # именно так wp_nav:14 (ответ 837) притворился заданием 2947 (ответ 13).
            if not c["key_numbers"]:
                continue
            missing = sorted(c["key_numbers"] - my_nums)
            if missing:
                continue
            confirmed.append({"twin_id": cid, "external_uid": c["external_uid"],
                              "course_id": c["course_id"], "answers": c["accepted"]})

        answers = {tuple(c["answers"]) for c in confirmed}
        if not confirmed:
            verdict = "no_twin"
        elif len(answers) == 1:
            verdict = "match"
        else:
            verdict = "conflict"
        stats[verdict] += 1
        results.append({"id": it["id"], "course_id": it["course_id"], "max_score": it["max_score"],
                        "verdict": verdict, "twins": confirmed[:6],
                        "answer": confirmed[0]["answers"] if verdict == "match" else None})

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Результат: {dict(stats)}")
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    asyncio.run(main(Path(args.items), Path(args.out)))
