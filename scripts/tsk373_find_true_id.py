# -*- coding: utf-8 -*-
"""tsk-373, шаг 3: найти настоящий ID задачи там, где номер в шапке ошибочен.

ЗАЧЕМ
После сверки с РОДНЫМ источником (`tsk373_verify_native.py`) остаются задания, у которых
и родной источник по этому ID отдаёт постороннюю задачу. Значит ошибочен сам номер, а не
ответ. Чтобы это доказать, а не предположить, надо предъявить задачу источника, условие
которой совпадает с условием LMS.

КАК ИЩЕТСЯ
Поиска по тексту ни один из трёх источников не даёт. Зато известен характер ошибки: в
[[tsk-369]] обе опечатки оказались опечатками набора — перестановка соседних цифр
(27360 → 23760) и замена последней (23746 → 23747). Поэтому перебираются «соседи по
опечатке» указанного ID; каждый кандидат сверяется теми же двумя признаками, что и
везде в задаче — дословный фрагмент условия и значимые числа.

Оба известных случая [[tsk-369]] служат контрольными: если перебор их не находит, метод
негоден и результатам по остальным верить нельзя.

Ничего не пишет в БД. На выходе JSON: найденный ID, признаки совпадения и совпал ли
ответ LMS с ключом НАСТОЯЩЕЙ задачи.

Запуск: python scripts/tsk373_find_true_id.py --native <файл.json> --cache <кэш.json>
                                              --out <файл.json> [--only 3036,3056]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk362_fetch_answers import GETTERS  # noqa: E402
from tsk370_scan import dsn, strip_html  # noqa: E402
from tsk373_classify import (answers_loose_equal, body_text, classify,  # noqa: E402
                             jaccard, strip_latex, words)

KOMPEGE_API = "https://kompege.ru/api/v1/task/{}"
PAUSE_SEC = 0.3
PREFILTER = 0.25  # дешёвый отсев по словарным сочетаниям до дорогого поиска фрагмента


def neighbours(task_id: str) -> list[str]:
    """ID-«соседи по опечатке» в порядке убывания вероятности ошибки набора."""
    out: list[str] = []
    d = list(task_id)
    for i in range(len(d) - 1):  # перестановка соседних цифр
        if d[i] != d[i + 1]:
            out.append("".join(d[:i] + [d[i + 1], d[i]] + d[i + 2:]))
    for i in range(len(d)):      # замена одной цифры
        for c in "0123456789":
            if c != d[i]:
                out.append("".join(d[:i] + [c] + d[i + 1:]))
    for i in range(len(d)):      # пропущенная цифра
        out.append("".join(d[:i] + d[i + 1:]))
    for i in range(len(d) + 1):  # лишняя цифра
        for c in "0123456789":
            out.append("".join(d[:i] + [c] + d[i:]))
    seen, uniq = {task_id}, []
    for c in out:
        c = c.lstrip("0")
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def get_task(kind: str, task_id: str, cache: dict) -> tuple[str | None, str] | None:
    """Ответ и условие задачи-кандидата; kompege кэшируется, остальные — живьём."""
    if kind == "kompege":
        if task_id not in cache:
            try:
                with urllib.request.urlopen(KOMPEGE_API.format(task_id), timeout=30) as r:
                    cache[task_id] = json.load(r)
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                cache[task_id] = {"error": str(exc)}
            time.sleep(PAUSE_SEC)
        src = cache[task_id]
        if not isinstance(src, dict) or src.get("error") or not src.get("text"):
            return None
        return (src.get("key") or None), strip_html(src["text"])
    try:
        answer, text = GETTERS[kind](task_id)
    except Exception:
        return None
    time.sleep(PAUSE_SEC)
    return (answer, text) if text and len(text) > 40 else None


async def load_stems(ids: list[int]) -> dict:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, task_content->>'stem' AS stem FROM tasks "
            "WHERE id = ANY($1::int[])", ids)
    finally:
        await conn.close()
    return {r["id"]: r["stem"] for r in rows}


def main(native_path: Path, cache_path: Path, out: Path,
         only: set[int] | None, max_cand: int) -> None:
    native = json.loads(native_path.read_text(encoding="utf-8"))
    todo = [r for r in native if r["verdict"] == "свой источник: задача другая"
            and (not only or r["id"] in only)]
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    stems = asyncio.run(load_stems([r["id"] for r in todo]))

    results = []
    for n, r in enumerate(todo, 1):
        stem = stems[r["id"]]
        lms_words = words(strip_latex(body_text(stem)))
        kind = r["source_kind"] or "kompege"
        found = None
        for cid in neighbours(r["header_id"])[:max_cand]:
            got = get_task(kind, cid, cache)
            if not got:
                continue
            answer, text = got
            if jaccard(lms_words, words(strip_latex(text))) < PREFILTER:
                continue
            f = classify(stem, text)
            same_ans = any(answers_loose_equal(v, answer)
                           for v in r["answer_lms_all"]) if answer else None
            if f["verdict"] == "same" or (f["verdict"] == "ambiguous" and same_ans):
                found = {"kompege_id": cid, "answer_true_src": answer,
                         "answer_matches_true": same_ans, **f}
                break
        if kind == "kompege":
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        results.append({**{k: r[k] for k in ("id", "source_kind", "header_number",
                                             "header_id", "answer_lms")},
                        "found": found})
        if found:
            print(f"[{n}/{len(todo)}] id={r['id']}: настоящий {kind} ID = "
                  f"{found['kompege_id']} (lcs={found['lcs']}, чис="
                  f"{found['numbers_src_cover']}), ответ источника "
                  f"{found['answer_true_src']!r} — "
                  f"{'совпал' if found['answer_matches_true'] else 'НЕ совпал'} с LMS")
        else:
            print(f"[{n}/{len(todo)}] id={r['id']}: настоящий ID не найден "
                  f"среди {max_cand} соседей {r['header_id']} у {kind}")
        sys.stdout.flush()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [x for x in results if x["found"]]
    print(f"\nнайдено: {len(ok)} из {len(results)}; из них ответ LMS равен ключу "
          f"настоящей задачи: {sum(1 for x in ok if x['found']['answer_matches_true'])}")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", type=Path, required=True)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", help="ограничить списком id через запятую")
    ap.add_argument("--max-candidates", type=int, default=120)
    a = ap.parse_args()
    main(a.native, a.cache, a.out,
         {int(x) for x in a.only.split(",")} if a.only else None, a.max_candidates)
