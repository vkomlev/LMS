# -*- coding: utf-8 -*-
"""tsk-362, шаг 3: свести источники в план записи (что авто, что в ручную, что не трогаем).

ВХОД
  * `items`   — рабочий список (шаг 1);
  * `fetched` — ответы с сайтов-источников и Яндекса (шаг 2), один или несколько файлов;
  * `twins`   — ответы близнецов внутри LMS (шаг 2b).

ПРАВИЛА СВЕДЕНИЯ
1. Берём только вердикт `match` (совпали и текст, и значимые числа). `weak`/`mismatch`
   в авто-ответ не идут никогда.
2. Приоритет у сайта-источника: ID там авторитетный, близнец — совпадение по тексту.
   Если оба дали ответ и ответы РАЗНЫЕ → `conflict`, задание уходит в ручную проверку.
3. Ответ, который не является одиночным скаляром (таблица, несколько ячеек, «3&625350»,
   перечисление через запятую/пробел) → ручная проверка без `accepted_answers`.
   Принцип [[tsk-325]]/[[tsk-100]]: слепой перенос многозначного ответа строкой делает
   задание «всегда неверно» — хуже честной ручной проверки.
4. Всё, для чего ответа нет, → ручная проверка (`manual_review_required=true`). Это не
   косметика: при пустом правиле движок возвращает `is_correct=None` и балл 0, но в
   ОБЯЗАТЕЛЬНУЮ очередь преподавателя задание не попадает (`teacher_queue_service`), и
   ответ ученика зависает непроверенным. С флагом — попадает.
5. Опросники-профилировщики (`solution_rules.quiz` не пуст, тип `SC_Qw`) исключаются:
   у них верного ответа не существует по замыслу, это не дефект.

Ничего не пишет в БД: на выходе план для шага 4 + отчёт по классам.

Запуск:
  python scripts/tsk362_build_plan.py --items i.json --twins t.json --fetched a.json b.json --out plan.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

# Одиночный короткий ответ: число, слово из букв/цифр, «-4», «12.5». Всё остальное
# (пробелы, «&», запятые, вложенные списки) — многозначное.
_SCALAR = re.compile(r"^-?[0-9A-Za-zА-Яа-яЁё.,]{1,40}$")


def flatten_answer(a):
    """Привести ответ источника к строке, если он одиночный; иначе вернуть None."""
    while isinstance(a, list):
        if len(a) != 1:
            return None
        a = a[0]
    if a is None:
        return None
    s = str(a).strip()
    if not s:
        return None
    if "&" in s or " " in s or "\n" in s:
        return None
    if "," in s and not re.fullmatch(r"-?\d+,\d+", s):
        return None
    return s if _SCALAR.fullmatch(s) else None


def main(items_p: Path, twins_p: Path, fetched_ps: list[str], out_p: Path) -> None:
    items = {i["id"]: i for i in json.loads(items_p.read_text(encoding="utf-8"))}
    twins = {t["id"]: t for t in json.loads(twins_p.read_text(encoding="utf-8"))}

    site: dict[int, dict] = {}
    for f in fetched_ps:
        for r in json.loads(Path(f).read_text(encoding="utf-8")):
            if r.get("verdict") == "match" and r.get("answer") is not None:
                site[r["id"]] = r

    plan = {"auto": [], "manual": [], "skip": [], "conflicts": []}
    for tid, it in items.items():
        # 5. Опросник — не дефект.
        # Признак опросника — непустой блок `quiz` в правилах. Ключ `scales` в
        # task_content для этого не годится: у части заданий он есть со значением
        # JSON-null (та же ловушка, что дала tsk-361).
        if it.get("is_quiz"):
            plan["skip"].append({"id": tid, "reason": "опросник-профилировщик, верного ответа нет"})
            continue

        s = site.get(tid)
        tw = twins.get(tid)
        s_ans = flatten_answer(s["answer"]) if s else None
        tw_ans = None
        if tw and tw.get("verdict") == "match" and tw.get("answer"):
            tw_ans = flatten_answer(tw["answer"])

        # 2. Расхождение между независимыми источниками — не выбираем «победителя».
        if s_ans and tw_ans and s_ans != tw_ans:
            plan["conflicts"].append({"id": tid, "site": s_ans, "twin": tw_ans,
                                      "source": s["source"], "source_id": s["source_id"]})
            plan["manual"].append({"id": tid, "reason": f"расхождение источников: сайт={s_ans}, близнец={tw_ans}"})
            continue

        value = s_ans or tw_ans
        if value:
            plan["auto"].append({
                "id": tid, "course_id": it["course_id"], "max_score": it["max_score"],
                "value": value,
                "origin": (f"{s['source']}:{s['source_id']}" if s_ans else
                           f"twin:{tw['twins'][0]['twin_id']}"),
                "via": it.get("via"),
            })
            continue

        # 3/4. Ответа нет или он многозначный — честная ручная проверка.
        if s and s.get("answer") is not None:
            reason = f"ответ источника многозначный ({str(s['answer'])[:60]})"
        elif tw and tw.get("verdict") == "conflict":
            reason = "близнецы с разными ответами"
        elif tw and tw.get("verdict") == "match":
            reason = f"ответ близнеца многозначный ({str(tw.get('answer'))[:60]})"
        else:
            reason = "источник ответа не найден"
        plan["manual"].append({"id": tid, "course_id": it["course_id"],
                               "max_score": it["max_score"], "reason": reason})

    out_p.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Авто (accepted_answers):      {len(plan['auto'])}")
    print(f"Ручная проверка:              {len(plan['manual'])}")
    print(f"Не трогаем (опросники):       {len(plan['skip'])}")
    print(f"  в т.ч. расхождений источников: {len(plan['conflicts'])}")
    for c in plan["conflicts"][:10]:
        print(f"    id={c['id']} сайт({c['source']}:{c['source_id']})={c['site']} vs близнец={c['twin']}")
    print(f"\nСохранено: {out_p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--twins", required=True)
    ap.add_argument("--fetched", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(Path(a.items), Path(a.twins), a.fetched, Path(a.out))
