# -*- coding: utf-8 -*-
"""Проверка: не пострадали ли скрытия от artifact strip_html (съедал $n<..>$).

Для каждого скрытого задания, чей сырой stem содержит математический < или >,
сверяем ПОЛНЫЙ сырой текст с каноном (без strip_html) — реально ли дубль.
"""
import sys, io, json, glob, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

rows = json.load(open("tasks_dump.json", encoding="utf-8"))
raw = {r["id"]: (json.loads(r["task_content"]) if isinstance(r["task_content"], str)
                 else r["task_content"]) for r in rows}
uidm = {r["id"]: r["external_uid"] for r in rows}

# собрать все пары (hide -> keep) из планов
pairs = []
for pf in ("plan_pass1.json", "plan_pass2.json", "prio1_plan.json",
           "final_plan.json", "degrees_plan.json"):
    try:
        pl = json.load(open(pf, encoding="utf-8"))
    except FileNotFoundError:
        continue
    for p in pl:
        for h in p["hide"]:
            pairs.append((h, p["keep"], pf))

MATH_LT = re.compile(r"\$[^$]*[<>][^$]*\$")


def clean(stem):
    """Аккуратно: убрать HTML-теги, но НЕ трогать содержимое $...$."""
    if not stem:
        return ""
    parts = re.split(r"(\$[^$]*\$)", stem)  # сохранить math-сегменты целиком
    out = []
    for seg in parts:
        if seg.startswith("$"):
            out.append(seg)
        else:
            out.append(re.sub(r"<[^>]+>", " ", seg))
    s = " ".join(out)
    return re.sub(r"\s+", " ", s).strip().lower()


affected = [(h, k) for h, k, pf in pairs
            if h in raw and MATH_LT.search(raw[h].get("stem") or "")]
print(f"всего скрыто: {len(set(h for h,_,_ in pairs))} | с математическим <>/: {len(affected)}")
print()
for h, k in affected:
    hs, ks = clean(raw[h].get("stem")), clean(raw.get(k, {}).get("stem"))
    # сравнить по множеству math-сегментов (формулы) — они различают задачи
    hm = set(re.findall(r"\$[^$]*\$", raw[h].get("stem") or ""))
    km = set(re.findall(r"\$[^$]*\$", raw.get(k, {}).get("stem") or ""))
    inter = len(hm & km)
    mn = min(len(hm), len(km)) or 1
    verdict = "OK дубль" if inter / mn >= 0.6 else "!!! ПРОВЕРИТЬ — формулы расходятся"
    print(f"скрыт {h} ({uidm[h]}) <- канон {k} ({uidm.get(k)})")
    print(f"   формул общих {inter}/{mn} -> {verdict}")
    if inter / mn < 0.6:
        print(f"   H формулы: {sorted(hm)[:6]}")
        print(f"   K формулы: {sorted(km)[:6]}")
