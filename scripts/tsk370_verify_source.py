# -*- coding: utf-8 -*-
"""tsk-370, шаг 2: сверить условия заданий с источником kompege и найти потерянный вопрос.

ЗАЧЕМ
Эвристика «в условии нет глагола постановки» ловит явные случаи, но маскируется словами
из преамбулы: у задания 2138 слово «сколько» встретилось в описании таблицы, и обрыв
условия остался незамеченным. Надёжнее сверить с источником: у kompege условие отдаётся
целиком по ID, и если ПОСЛЕДНИХ предложений источника в LMS нет — вопрос потерян при
импорте, а не «так задумано».

ОТКУДА БЕРЁТСЯ ID ИСТОЧНИКА (в порядке надёжности)
  1. `task_content.source_kind = 'kompege'` + `source_task_id` — партия wp_nav;
  2. `external_uid` вида `ext:<партия>:kompege:<дата>:<id>`;
  3. шапка условия «Задание NN_<id>» у партии `tg:*` (у части — с суффиксом `k`).

Ответы API кэшируются в файл: повторный прогон не ходит в сеть.
Ничего не пишет в БД. На выходе JSON со списком расхождений.

Запуск:  python scripts/tsk370_verify_source.py --cache <кэш.json> --out <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
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
from tsk370_scan import dsn, sentences, strip_html  # noqa: E402

API = "https://kompege.ru/api/v1/task/{}"
PAUSE_SEC = 0.25  # пауза между запросами к чужому сайту
PROBE_LEN = 45    # сколько символов хвостового предложения ищем в условии LMS


def norm(s: str) -> str:
    """Текст для сравнения: без разметки, регистра, пунктуации и служебных пробелов."""
    s = strip_html(s or "").lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я]+", "", s)
    return s


# Источник, названный в шапке условия рядом с номером задачи. Проверяется по первым
# 400 символам: дальше начинается само условие, где «Поляков» может встретиться по делу.
FOREIGN_SRC_RE = re.compile(r"поляков|решу\s*егэ|сдамгиа|sdamgia|яндекс", re.I)


def kompege_id(external_uid: str, source_kind: str | None,
               source_task_id: str | None, stem: str) -> str | None:
    """ID задачи у kompege по трём признакам, см. докстринг.

    Номер из шапки — САМЫЙ слабый из трёх: у партии `tg:ege` рядом с номером написан и
    источник, и там сплошь «(Поляков)» и «(Решу ЕГЭ)». Такой ID принадлежит их базе, а не
    kompege, и запрос по нему возвращает постороннюю задачу — из 23 «расхождений ответа»
    [[tsk-373]] 17 оказались именно этим (дефекта в LMS нет, сверка шла не на тот сайт).
    Поэтому чужой источник — и записанный в `source_kind`, и просто названный в шапке —
    отменяет разбор номера из шапки.
    """
    if source_kind == "kompege" and source_task_id and source_task_id.isdigit():
        return source_task_id
    if source_kind and source_kind != "kompege":
        return None
    parts = (external_uid or "").split(":")
    if len(parts) >= 5 and parts[0] == "ext" and parts[2] == "kompege" and parts[4].isdigit():
        return parts[4]
    if (external_uid or "").startswith("tg:"):
        text = strip_html(stem or "")
        if FOREIGN_SRC_RE.search(text[:400]):
            return None
        m = re.search(r"Задание\s+\d+[_ ](\d+)k?\b", text)
        if m:
            return m.group(1)
    return None


def fetch(task_id: str, cache: dict) -> dict | None:
    """Задача источника по ID; ответы кэшируются, ошибки не роняют прогон."""
    if task_id in cache:
        return cache[task_id]
    try:
        with urllib.request.urlopen(API.format(task_id), timeout=30) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        cache[task_id] = {"error": str(exc)}
        return cache[task_id]
    time.sleep(PAUSE_SEC)
    cache[task_id] = data
    return data


def compare(lms_stem: str, src_text: str) -> dict:
    """Чего из хвоста источника нет в условии LMS."""
    lms_n = norm(lms_stem)
    src_sents = [s for s in sentences(strip_html(src_text)) if len(s) > 25]
    missing = []
    for s in src_sents[-3:]:
        probe = norm(s)[:PROBE_LEN]
        if probe and probe not in lms_n:
            missing.append(s[:300])
    return {
        "missing_tail": missing,
        "src_len": len(strip_html(src_text)),
        "lms_len": len(strip_html(lms_stem)),
    }


async def main(cache_path: Path, out: Path) -> None:
    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, task_content->>'stem' AS stem, "
            "       task_content->>'source_kind' AS source_kind, "
            "       task_content->>'source_task_id' AS source_task_id, "
            "       solution_rules #>> '{short_answer,accepted_answers,0,value}' AS answer "
            "FROM tasks WHERE is_active = true ORDER BY id")
    finally:
        await conn.close()

    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    targets = []
    for r in rows:
        tid = kompege_id(r["external_uid"], r["source_kind"], r["source_task_id"], r["stem"])
        if tid:
            targets.append((r, tid))
    print(f"заданий со ссылкой на kompege: {len(targets)}")

    findings, errors, answer_mismatch = [], [], []
    for n, (r, tid) in enumerate(targets, 1):
        src = fetch(tid, cache)
        if n % 50 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  ...{n}/{len(targets)}")
        if not src or src.get("error") or not src.get("text"):
            errors.append({"id": r["id"], "kompege_id": tid,
                           "why": (src or {}).get("error", "пустой ответ")})
            continue
        cmp = compare(r["stem"], src["text"])
        same_answer = (str(src.get("key", "")).strip().lower()
                       == str(r["answer"] or "").strip().lower())
        if cmp["missing_tail"]:
            findings.append({
                "id": r["id"], "external_uid": r["external_uid"], "kompege_id": tid,
                "answer_lms": r["answer"], "answer_src": src.get("key"),
                "answer_match": same_answer, "number": src.get("number"),
                **cmp,
            })
        if not same_answer:
            answer_mismatch.append({"id": r["id"], "kompege_id": tid,
                                    "lms": r["answer"], "src": src.get("key")})

    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"checked": len(targets), "findings": findings,
         "errors": errors, "answer_mismatch": answer_mismatch},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"хвост источника не найден в условии LMS: {len(findings)}")
    print(f"  из них ответ LMS совпал с источником: "
          f"{sum(1 for f in findings if f['answer_match'])}")
    print(f"ответ разошёлся с источником (все сверенные): {len(answer_mismatch)}")
    print(f"источник не ответил: {len(errors)}")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(main(args.cache, args.out))
