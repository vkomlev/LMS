# -*- coding: utf-8 -*-
"""tsk-350: разбор приоритета 1 (пары «разные приложенные файлы»).

Классифицирует 26 пар, кластеризует дубли (транзитивно), выбирает канон
с приоритетом «есть файл данных» → лучший источник, готовит отчёт со ссылками.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

from detect import find_groups, build
from analyze_files import classify, file_shas, CONTENT_CHECKED
from links import lms_url, source_link

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"D:\Work\LMS\reviews\2026-07-23-tsk350-prioritet1-razbor.md")


def confidence(a: dict, b: dict, c: dict) -> tuple[str, str]:
    """(уровень, пояснение вердикта дубля)."""
    fs = frozenset((a["id"], b["id"]))
    if fs in CONTENT_CHECKED:
        v = CONTENT_CHECKED[fs]
        return ("не дубль", "содержимое файлов реально разное") if v.startswith("НЕ") \
            else ("точно", "содержимое файлов сверено — совпало")
    if c["file_state"] == "одинаковый файл (sha)":
        return "точно", "приложен байт-в-байт один файл (совпал sha256)"
    if c["src_state"] == "источник+ID совпали":
        return "точно", f"один первоисточник {c['src_a'][0]} #{c['src_a'][1]}"
    # кросс-банк: совпал ответ?
    ans = (a["answer"] or "").replace("SA:", "")
    digits = len(re.sub(r"\D", "", ans))
    if a["answer"] and a["answer"] == b["answer"]:
        if digits >= 4:
            return "точно", f"разные банки, но совпал многозначный ответ {ans} " \
                             "(случайность исключена) + текст + числа"
        return "вероятно", f"разные банки, совпал ответ {ans} + текст + числа + формула " \
                           "(короткий ответ — стоит взглянуть)"
    return "смотреть", "совпал текст, но ответ подтвердить нечем"


def has_file(r: dict) -> bool:
    return bool(file_shas(r))


def src_rank(r: dict) -> int:
    u = r["external_uid"] or ""
    if u.startswith("ext:"):
        return 0
    if u.startswith("wp_nav"):
        return 1
    return 2


def canon_of(members: list[dict]) -> dict:
    return min(members, key=lambda r: (not has_file(r), src_rank(r), r["id"]))


def block(r: dict, mark: str) -> str:
    lbl, src = source_link(r)
    f = "есть файл данных" if has_file(r) else "БЕЗ файла данных"
    o = f"- **{mark}** [{r['id']} — в LMS]({lms_url(r)}) · {f} · "
    o += f"[{lbl}]({src})" if src else f"источник: {lbl}"
    return o + "\n"


class DSU:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def main() -> None:
    _, weak = find_groups()
    rows, _ = build()
    d = {r["id"]: r for r in rows}
    pairs = [w for w in weak if w["reason"].startswith("разные приложенные файлы")]

    # классификация каждой пары
    enriched = []
    dsu = DSU()
    for w in pairs:
        a, b = w["members"]
        c = classify(a, b)
        level, why = confidence(a, b, c)
        enriched.append({"a": a, "b": b, "c": c, "level": level, "why": why,
                         "containment": w["containment"]})
        if level in ("точно", "вероятно"):
            dsu.union(a["id"], b["id"])

    # кластеры дублей
    clusters: dict[int, set[int]] = {}
    for e in enriched:
        if e["level"] in ("точно", "вопрос", "вероятно"):
            root = dsu.find(e["a"]["id"])
            clusters.setdefault(root, set()).update((e["a"]["id"], e["b"]["id"]))

    o = io.StringIO()
    o.write("# tsk-350 — разбор приоритета 1 («разные приложенные файлы»)\n\n")
    o.write("26 пар, где текст/числа/формула/ответ совпали, а различались приложенные "
            "файлы. Разбор: имя файла `/api/v1/media/<sha256>.<ext>` — это SHA256 самого "
            "содержимого (CAS-хранилище), поэтому **хэш уже посчитан и лежит в имени**: "
            "одинаковое имя = байт-в-байт один файл. Плюс сверка ID первоисточника и "
            "(для 3 пар «разные байты») скачивание файлов с прода и сравнение содержимого.\n\n")

    # сводка
    from collections import Counter
    lv = Counter(e["level"] for e in enriched)
    o.write("## Итог\n\n")
    o.write(f"- **точно дубль:** {lv['точно']} пар\n")
    o.write(f"- **вероятно дубль** (совпал короткий ответ, стоит взглянуть): {lv['вероятно']} пар\n")
    o.write(f"- **не дубль** (файлы реально разные): {lv['не дубль']} пар\n")
    o.write(f"- Кластеров дублей (с учётом транзитивности): {len(clusters)}\n\n")
    o.write("Как определялся дубль: (1) совпал sha файла → один файл; (2) совпал ID "
            "первоисточника → одна задача; (3) для разных банков — совпал текст + числа + "
            "формула + **ответ** (многозначный ответ исключает случайность); (4) три пары "
            "«разные байты» сверены по содержимому — две оказались тем же файлом в другом "
            "формате (xls↔ods, csv с `,` и `;`), одна — реально разными данными.\n\n")

    o.write("## Кластеры дублей — рекомендация к скрытию\n\n")
    o.write("Канон = версия **с файлом данных** (иначе задание без данных нерешаемо), "
            "при равенстве — лучший источник (каталог `ext:d4` > навигатор > ТГ-разбор).\n\n")
    for n, (root, ids) in enumerate(sorted(clusters.items(), key=lambda kv: min(kv[1])), 1):
        members = [d[i] for i in sorted(ids)]
        canon = canon_of(members)
        # уровень кластера — худший из входящих пар
        lvls = [e["level"] for e in enriched
                if e["a"]["id"] in ids and e["b"]["id"] in ids]
        clv = "вероятно" if "вероятно" in lvls else "точно"
        o.write(f"### Кластер {n} — {'ТОЧНО дубли' if clv=='точно' else 'вероятные дубли'} "
                f"({len(members)} задания)\n\n")
        # пояснение из первой пары кластера
        why = next(e["why"] for e in enriched
                   if e["a"]["id"] in ids and e["b"]["id"] in ids)
        o.write(f"_{why}_\n\n")
        o.write(block(canon, "ОСТАВИТЬ (канон)"))
        for m in members:
            if m["id"] != canon["id"]:
                o.write(block(m, "скрыть"))
        o.write("\n")

    o.write("## Не дубль\n\n")
    for e in enriched:
        if e["level"] == "не дубль":
            a, b = e["a"], e["b"]
            o.write(f"- {a['id']} / {b['id']} — {e['why']}. Оставить оба.\n")
            o.write(block(a, "оставить") + block(b, "оставить") + "\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(o.getvalue(), encoding="utf-8")
    print("отчёт:", OUT)
    # список к скрытию
    hide = []
    for root, ids in clusters.items():
        members = [d[i] for i in ids]
        canon = canon_of(members)
        hide += [i for i in ids if i != canon["id"]]
    print("кластеров:", len(clusters), "| к скрытию заданий:", len(hide))
    print("точно:", lv["точно"], "вероятно:", lv["вероятно"], "не дубль:", lv["не дубль"])
    Path("prio1_hide.txt").write_text(
        ",".join(str(i) for i in sorted(hide)), encoding="utf-8")


if __name__ == "__main__":
    main()
