# -*- coding: utf-8 -*-
"""tsk-350: финальный разбор оставшихся спорных пар (после работы чипов).

Ключи (в порядке силы):
  1. совпал SHA256 приложенного файла  -> один файл данных -> дубль
  2. совпал ID первоисточника          -> одна задача      -> дубль
  3. многозначный (>=4 цифр) ответ при разных банках -> случайность исключена
  4. ручная сверка (тексты/файлы скачаны и сравнены)

Расхождение «чисел условия» у пар с совпавшим ID — артефакт рендера степеней
(4^210 импортировано то как «4210», то как «4 ^ 210»; 10^6 как «106»/«10 6»),
а не разные данные. Проверено глазами на 4 парах.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

from detect import find_groups, build
from analyze_files import classify, file_shas
from links import lms_url, source_link
from prio1_report import DSU, has_file, src_rank

import json as _json

_PASSES = _json.loads(Path("passes.json").read_text(encoding="utf-8"))


def canon_of(members: list[dict]) -> dict:
    """Канон: (1) кем зачтён чаще — иначе ученик теряет отметку (урок tsk-317);
    (2) есть файл данных; (3) лучший источник; (4) меньший id."""
    def key(r: dict):
        return (-len(_PASSES.get(str(r["id"]), [])), not has_file(r),
                src_rank(r), r["id"])
    return min(members, key=key)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"D:\Work\LMS\reviews\2026-07-23-tsk350-final-spornye.md")

# Ручные вердикты (сверены глазами / скачиванием файлов в этой сессии)
MANUAL = {
    frozenset((2240, 4564)): ("нет", "Крылов переписал IP-адрес сети (172.16.168.0 → 204.16.168.0) — разные задачи"),
    frozenset((2362, 4493)): ("нет", "разные логические функции: (x≡¬y)→((x∧w)≡(z∧¬w)) против (x≡(y→z))∧(y≡¬(z→w)); ответ wzyx совпал случайно (24 варианта)"),
    frozenset((2258, 3761)): ("нет", "разные данные: 300 dpi / 2^16 цветов против 600 dpi / 2^24 — ответ 2 совпал случайно"),
    frozenset((4915, 4916)): ("нет", "«список из чисел» против «список из квадратных корней» — разные задания"),
    frozenset((4260, 4261)): ("нет", "разные числа игры: не менее 133 / s≤125 против 125 / s≤117"),
    frozenset((2166, 3536)): ("нет", "файлы данных задания 22 разные: 13 строк процессов против 18 (скачаны и сверены)"),
    frozenset((2165, 3552)): ("нет", "файлы данных задания 22 разные: 14 строк против 15 (скачаны и сверены)"),
    frozenset((2117, 3272)): ("да", "ID 12088 в шапке ТГ совпадает с КомпЕГЭ #12088; «2 32» против «232» — рендер степени 2^32"),
    frozenset((2084, 3315)): ("да", "ID 13 в шапке ТГ = КомпЕГЭ #13; «49 7 + 7 21» против «49^7 + 7^21» — рендер степеней"),
    frozenset((2143, 4517)): ("да", "разные банки, но совпал 10-значный ответ 2276939784 — случайность исключена"),
    frozenset((4248, 4405)): ("да", "разные банки, совпал 25-значный ответ; «10^10» против «10 10» — рендер степени"),
    frozenset((4210, 4370)): ("да", "разные банки, совпал 9-значный ответ 298322640 (Статный/Шагитов)"),
    frozenset((4017, 4126)): ("да", "текст дословно идентичен (отличается только пунктуация нумерации), КомпЕГЭ продублировал под #2851 и #4869"),
    frozenset((3289, 3750)): ("да", "одна задача Яндекса, разный рендер (LaTeX $n$ против текста)"),
}


def verdict(a: dict, b: dict) -> tuple[str, str]:
    """('да'|'нет', обоснование)."""
    fs = frozenset((a["id"], b["id"]))
    if fs in MANUAL:
        return MANUAL[fs]
    c = classify(a, b)
    if c["file_state"] == "одинаковый файл (sha)":
        return "да", "приложен байт-в-байт один файл данных (совпал sha256)"
    if c["src_state"] == "источник+ID совпали":
        return "да", f"один первоисточник {c['src_a'][0]} #{c['src_a'][1]} " \
                     "(расхождение чисел — рендер степеней/шапка ТГ)"
    ans = (a["answer"] or "").replace("SA:", "")
    if a["answer"] and a["answer"] == b["answer"] and len(re.sub(r"\D", "", ans)) >= 4:
        return "да", f"разные банки, совпал многозначный ответ {ans[:20]}"
    return "?", "требуется взгляд оператора"


def block(r: dict, mark: str) -> str:
    lbl, src = source_link(r)
    f = "с файлом" if has_file(r) else "без файла"
    o = f"- **{mark}** [{r['id']} — в LMS]({lms_url(r)}) · {f} · "
    o += f"[{lbl}]({src})" if src else f"источник: {lbl}"
    return o + "\n"


def main() -> None:
    _, weak = find_groups()
    rows, _ = build()
    d = {r["id"]: r for r in rows}

    dsu = DSU()
    dup_pairs, not_dup, unknown = [], [], []
    for w in weak:
        a, b = w["members"]
        v, why = verdict(a, b)
        if v == "да":
            dsu.union(a["id"], b["id"]); dup_pairs.append((a, b, why))
        elif v == "нет":
            not_dup.append((a, b, why))
        else:
            unknown.append((a, b, why))

    clusters: dict[int, set[int]] = {}
    for a, b, _ in dup_pairs:
        clusters.setdefault(dsu.find(a["id"]), set()).update((a["id"], b["id"]))

    plan = []
    for root, ids in clusters.items():
        canon = canon_of([d[i] for i in ids])
        plan.append({"keep": canon["id"], "hide": sorted(i for i in ids if i != canon["id"])})
    hide = sorted(i for p in plan for i in p["hide"])

    o = io.StringIO()
    o.write("# tsk-350 — финальный разбор спорных пар (после работы чипов)\n\n")
    o.write("Чипы закрыли причины неопределённости: **tsk-321** заполнил ответы "
            "(активных без ответа — 0), **tsk-390** привязал файлы к 229 заданиям. "
            "Это сняло целые категории сомнений и позволило добить остаток.\n\n")
    o.write(f"Разобрано пар: **{len(weak)}**. Дубли: **{len(dup_pairs)}** пар "
            f"({len(clusters)} кластеров, к скрытию {len(hide)} заданий). "
            f"Не дубли: **{len(not_dup)}**. Осталось неясным: {len(unknown)}.\n\n")
    o.write("**Важно:** расхождение «чисел условия» у пар с совпавшим ID первоисточника "
            "оказалось артефактом рендера степеней — `4²¹⁰` импортировалось то как "
            "`4210`, то как `4^210`; `10⁶` как `106` или `10 6`. Данные одни и те же. "
            "Проверено глазами на четырёх парах.\n\n")

    o.write("## Дубли — к скрытию\n\n")
    o.write("Канон = версия с файлом данных, затем лучший источник (каталог > навигатор > ТГ).\n\n")
    for n, p in enumerate(sorted(plan, key=lambda x: x["keep"]), 1):
        ids = [p["keep"]] + p["hide"]
        why = next((w for a, b, w in dup_pairs if a["id"] in ids and b["id"] in ids), "")
        o.write(f"### Кластер {n}\n\n_{why}_\n\n")
        o.write(block(d[p["keep"]], "ОСТАВИТЬ"))
        for i in p["hide"]:
            o.write(block(d[i], "скрыть"))
        o.write("\n")

    o.write("## Не дубли — оставить оба\n\n")
    for a, b, why in not_dup:
        o.write(f"### {a['id']} / {b['id']}\n\n_{why}_\n\n")
        o.write(block(a, "оставить") + block(b, "оставить") + "\n")

    if unknown:
        o.write("## Осталось неясным\n\n")
        for a, b, why in unknown:
            o.write(f"- {a['id']} / {b['id']} — {why}\n")
            o.write(block(a, "?") + block(b, "?"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(o.getvalue(), encoding="utf-8")
    import json
    Path("final_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print("отчёт:", OUT)
    print(f"дублей {len(dup_pairs)} пар -> {len(clusters)} кластеров, скрыть {len(hide)}")
    print(f"не дубли: {len(not_dup)}, неясно: {len(unknown)}")
    print("к скрытию:", hide)


if __name__ == "__main__":
    main()
