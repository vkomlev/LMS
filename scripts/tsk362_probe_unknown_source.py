# -*- coding: utf-8 -*-
"""tsk-362, добор: подобрать источник перебором там, где он не назван в шапке.

ЗАЧЕМ
У части заданий из Telegram в шапке есть числовой ID, но нет имени источника
(«Задание 13_23749 Демоверсия 2026», «Задание 5_25344 ЕГКР (московский пробник)»), а ссылки
в посте не сохранилось. Источник при этом определяется однозначно — перебором: тот же ID у
kompege, sdamgia и kpolyakov ведёт на разные задачи, и только у одного из них условие
совпадёт с тем, что лежит в LMS.

Гейт сверки — тот же, что в основном проходе (`tsk362_fetch_answers.verdict_for`): дословный
фрагмент условия + значимые числа. Если совпало у нескольких источников (маловероятно) —
кандидат отбрасывается как неоднозначный.

Ничего не пишет в БД: на выходе файл в формате шага 2, пригодный для `tsk362_build_plan.py`.

Запуск:
  python scripts/tsk362_probe_unknown_source.py --items items.json --ids 3035,3055,... --out probe.json
  python scripts/tsk362_probe_unknown_source.py --items items.json --all-unknown --out probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tsk362_fetch_answers import GETTERS, verdict_for  # noqa: E402

_STEM_ID = re.compile(r"(?:Задани[ея]|задани[ея])[^_<]{0,16}_(\d+)")


def main(items_path: Path, ids: list[int] | None, out_path: Path) -> None:
    items = json.loads(items_path.read_text(encoding="utf-8"))
    by_id = {i["id"]: i for i in items}
    if ids:
        targets = [by_id[i] for i in ids if i in by_id]
    else:
        targets = [i for i in items if not i.get("source")]

    results, stats = [], {"match": 0, "none": 0, "ambiguous": 0}
    for it in targets:
        m = _STEM_ID.search(it["stem"])
        if not m:
            stats["none"] += 1
            continue
        sid = m.group(1)
        hits = []
        for src, getter in GETTERS.items():
            try:
                answer, text = getter(sid)
                time.sleep(0.5)
            except Exception as exc:  # noqa: BLE001 — перебор источников, любая ошибка = «не тот»
                continue
            if not text:
                continue
            verdict, detail = verdict_for(it["stem"], text)
            if verdict == "match":
                hits.append((src, answer, detail, text))

        if len(hits) == 1:
            src, answer, detail, text = hits[0]
            results.append({"id": it["id"], "course_id": it["course_id"],
                            "max_score": it["max_score"], "source": src, "source_id": sid,
                            "via": "probe", "answer": answer, "verdict": "match",
                            "detail": detail, "src_text": text[:6000]})
            stats["match"] += 1
            print(f"  [match ] id={it['id']} → {src}:{sid} = {str(answer)[:40]!r}")
        elif len(hits) > 1:
            stats["ambiguous"] += 1
            print(f"  [ambig ] id={it['id']} ID {sid} совпал у {[h[0] for h in hits]} — не беру")
        else:
            stats["none"] += 1
            print(f"  [none  ] id={it['id']} ID {sid} не подошёл ни одному источнику")

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИтого: {stats}\nСохранено: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--ids", help="через запятую")
    ap.add_argument("--all-unknown", action="store_true", help="все задания без источника")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ids = [int(x) for x in a.ids.split(",")] if a.ids else None
    main(Path(a.items), ids, Path(a.out))
