# -*- coding: utf-8 -*-
"""tsk-373, шаг 2б: сверить расхождения с РОДНЫМ источником задания, а не только с kompege.

ЗАЧЕМ
Аудит [[tsk-370]] считает «заданием kompege» всё, у чего в шапке стоит «Задание NN_<id>».
Но у партии `tg:ege` в шапке рядом с номером написан и сам источник, и там сплошь и рядом
«(Поляков)» или «(Решу ЕГЭ)». ID из такой шапки — это `topicId` kpolyakov или `id` sdamgia,
и спрашивать по нему kompege бессмысленно: он вернёт свою, постороннюю задачу. Отсюда
берётся значительная часть «расхождений ответа» — их источник не тот, а не ответ неверный.

ЧТО ДЕЛАЕТ
По маркеру источника в ШАПКЕ условия (сырого `stem`, до нормализации) выбирает сайт,
забирает задачу по тому же ID его собственным способом ([[tsk-362]], `tsk362_fetch_answers`)
и сверяет двумя признаками — дословный фрагмент условия и значимые числа (`tsk373_classify`),
плюс сравнивает ответ LMS с ответом родного источника.

Вердикты:
  * `свой источник: задача та же, ответ тот же` — дефекта нет, это ложная тревога сверки
    с kompege;
  * `свой источник: задача та же, ответ РАСХОДИТСЯ` — спорное, оператору ([[tsk-368]]);
  * `свой источник: задача другая` — номер в шапке ошибочен и у родного источника;
  * `источник не опознан / не ответил` — на ручной разбор.

Только чтение, ничего не пишет в БД.

Запуск: python scripts/tsk373_verify_native.py --classify <файл.json> --out <файл.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import asyncpg

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk362_fetch_answers import GETTERS  # noqa: E402
from tsk370_scan import dsn, strip_html  # noqa: E402
from tsk373_classify import HEADER_RE, answers_loose_equal, classify  # noqa: E402

# Маркер источника ищется в шапке — первых 400 символах условия без разметки.
MARKERS = [
    ("polyakov", re.compile(r"поляков", re.I)),
    ("sdamgia", re.compile(r"решу\s*егэ|сдамгиа|sdamgia", re.I)),
    ("kompege", re.compile(r"кегэ|компегэ|kompege|демоверси", re.I)),
]
PAUSE_SEC = 0.7


def source_of(stem: str) -> tuple[str | None, str | None, str | None]:
    """Источник, номер задания ЕГЭ и ID из шапки условия."""
    text = strip_html(stem or "")
    head = text[:400]
    m = HEADER_RE.search(text)
    kind = next((k for k, rx in MARKERS if rx.search(head)), None)
    return kind, (m.group(1) if m else None), (m.group(2) if m else None)


async def main(classify_path: Path, out: Path) -> None:
    cls = json.loads(classify_path.read_text(encoding="utf-8"))
    ids = [m["id"] for m in cls["mismatch"]]
    by_id = {m["id"]: m for m in cls["mismatch"]}

    conn = await asyncpg.connect(dsn("learn_prod_db"))
    try:
        rows = await conn.fetch(
            "SELECT id, external_uid, task_content->>'stem' AS stem "
            "FROM tasks WHERE id = ANY($1::int[]) ORDER BY id", ids)
    finally:
        await conn.close()

    results = []
    for r in rows:
        item = by_id[r["id"]]
        kind, number, hid = source_of(r["stem"])
        row = {"id": r["id"], "external_uid": r["external_uid"],
               "source_kind": kind, "header_number": number, "header_id": hid,
               "answer_lms": item["answer_lms"], "answer_lms_all": item["answer_lms_all"],
               "answer_kompege_by_id": item["answer_src"]}
        candidates = [kind] if kind else ["polyakov", "sdamgia", "kompege"]
        best = None
        for src_kind in candidates:
            if not hid:
                break
            try:
                answer, text = GETTERS[src_kind](hid)
            except Exception as exc:  # сеть/разметка — не роняем перебор
                row.setdefault("errors", []).append(f"{src_kind}: {exc}")
                continue
            time.sleep(PAUSE_SEC)
            feats = classify(r["stem"], text)
            same_ans = any(answers_loose_equal(v, answer)
                           for v in item["answer_lms_all"]) if answer else None
            cand = {"kind": src_kind, "answer_src": answer, "same_answer": same_ans,
                    "src_head": feats["src_head"], **{k: feats[k] for k in
                                                      ("verdict", "lcs", "jaccard",
                                                       "numbers_src_cover")}}
            if best is None or (cand["verdict"] == "same" and best["verdict"] != "same"):
                best = cand
            if cand["verdict"] == "same":
                break
        # Второй допуск для «неоднозначно»: дословный фрагмент длинный, но значимые числа
        # сходятся плохо. Так выглядят задачи, где данные вынесены в приложенный файл —
        # чисел в условии почти нет, а преамбула общая. Признать «той же задачей» можно,
        # только если вместо чисел сошёлся ВТОРОЙ независимый признак — верный ответ.
        # Одного текста мало ([[feedback_fuzzy_match_shared_preamble]]), одного ответа —
        # тем более ([[tsk-369]], привязка чужого файла по совпавшему ответу).
        if best and best["verdict"] == "ambiguous" and best["same_answer"]:
            best = {**best, "verdict": "same", "same_by": "текст + ответ (числа не сошлись)"}

        row["native"] = best
        if not best:
            row["verdict"] = "источник не опознан"
        elif best["verdict"] != "same":
            row["verdict"] = "свой источник: задача другая"
        elif best["same_answer"] is None:
            row["verdict"] = "свой источник: задача та же, ответа нет"
        elif best["same_answer"]:
            row["verdict"] = "свой источник: задача та же, ответ тот же"
        else:
            row["verdict"] = "свой источник: задача та же, ответ РАСХОДИТСЯ"
        results.append(row)
        b = best or {}
        print(f"id={r['id']:<5} {str(kind):9} {str(hid):>7} → {row['verdict']}"
              f"  (lcs={b.get('lcs')}, чис={b.get('numbers_src_cover')}, "
              f"LMS={item['answer_lms']!r} vs {b.get('answer_src')!r})")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    by_v: dict[str, int] = {}
    for r in results:
        by_v[r["verdict"]] = by_v.get(r["verdict"], 0) + 1
    print()
    for v, n in sorted(by_v.items(), key=lambda x: -x[1]):
        print(f"  {v}: {n}")
    print(f"Выгрузка: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--classify", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    asyncio.run(main(a.classify, a.out))
