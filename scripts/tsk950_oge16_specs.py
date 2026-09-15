# -*- coding: utf-8 -*-
"""tsk-950: спецификации 30 заданий ОГЭ-16 (курс 1181) и шаблоны эталонов.

Каждое задание описано семейством и параметрами; эталоны собираются из шаблонов,
чтобы 30 заданий не превращались в 120 копипаст. Проверка: каждый эталон
исполняется на примере из условия и обязан дать ожидаемый вывод.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Спецификации. Семейства:
#   A  — N, затем N чисел; предикат `div k` / `end d`; агрегат max/min/sum/count
#   B  — ввод до 0; сумма чисел, кратных 6 и оканчивающихся на d
#   C  — 7242: ввод до 0; сумма двух наибольших и двух наименьших
#   D  — камера: N, затем скорости; max/min/avg + YES/NO
# ---------------------------------------------------------------------------

SPECS: Dict[int, dict] = {
    7226: dict(fam="A", agg="max", pred=("div", 5), sample=("3\n10\n25\n12\n", "25")),
    7227: dict(fam="A", agg="sum", pred=("div", 6), sample=("3\n12\n25\n6\n", "18")),
    7228: dict(fam="A", agg="count", pred=("div", 4), sample=("3\n16\n26\n24\n", "2")),
    7229: dict(fam="A", agg="min", pred=("div", 3), sample=("3\n21\n12\n31\n", "12")),
    7230: dict(fam="A", agg="sum", pred=("div", 3), sample=("3\n12\n25\n9\n", "21")),
    7231: dict(fam="A", agg="count", pred=("div", 6), sample=("3\n18\n26\n24\n", "2")),
    7232: dict(fam="A", agg="max", pred=("div", 4), sample=("3\n8\n16\n11\n", "16")),
    7233: dict(fam="A", agg="sum", pred=("end", 4), sample=("3\n14\n25\n24\n", "38")),
    7234: dict(fam="A", agg="count", pred=("end", 3), sample=("3\n13\n23\n24\n", "2")),
    7235: dict(fam="A", agg="max", pred=("end", 3), sample=("3\n13\n23\n3\n", "23")),
    7236: dict(fam="A", agg="min", pred=("end", 6), sample=("3\n26\n16\n36\n", "16")),
    7237: dict(fam="A", agg="sum", pred=("end", 3), sample=("3\n13\n23\n24\n", "36")),
    7238: dict(fam="A", agg="count", pred=("end", 6), sample=("3\n16\n26\n24\n", "2")),
    7239: dict(fam="A", agg="sum", pred=("div", 5), sample=("3\n15\n25\n6\n", "40")),
    7240: dict(fam="A", agg="count", pred=("div", 3), sample=("3\n12\n26\n24\n", "2")),
    7241: dict(fam="B", end=4, sample=("14\n24\n144\n22\n12\n0\n", "168")),
    7242: dict(fam="C", sample=("3\n10\n25\n12\n0\n", "37\n13")),
    7243: dict(fam="D", agg="max", cond=("<", 30), sample=("4\n74\n69\n63\n66\n", "74\nNO")),
    7244: dict(fam="D", agg="min", cond=(">", 80), sample=("4\n74\n69\n63\n96\n", "63\nYES"),
               extra=[("3\n50\n81\n65\n", "50\nYES"), ("1\n80\n", "80\nNO"),
                      ("2\n13\n52\n", "13\nNO"), ("3\n15\n140\n25\n", "15\nYES")]),
    7245: dict(fam="D", agg="avg", cond=(">=", 60), sample=("4\n74\n69\n63\n96\n", "75.5\nYES")),
    7246: dict(fam="B", end=4, sample=("24\n6\n34\n22\n84\n0\n", "108")),
    7247: dict(fam="B", end=4, sample=("54\n28\n72\n34\n24\n0\n", "78")),
    7248: dict(fam="B", end=2, sample=("12\n24\n22\n72\n11\n0\n", "84")),
    7249: dict(fam="A", agg="max", pred=("end", 2), sample=("4\n3\n22\n6\n12\n", "22")),
    7250: dict(fam="B", end=4, sample=("14\n24\n36\n84\n66\n0\n", "108")),
    7251: dict(fam="B", end=6, sample=("36\n12\n16\n66\n11\n0\n", "102")),
    7252: dict(fam="A", agg="sum", pred=("end", 8), sample=("3\n18\n28\n24\n", "46")),
    7253: dict(fam="A", agg="count", pred=("end", 9), sample=("3\n19\n29\n24\n", "2")),
    7254: dict(fam="A", agg="max", pred=("end", 9), sample=("3\n9\n19\n23\n", "19")),
    7255: dict(fam="A", agg="min", pred=("end", 2), sample=("3\n22\n12\n36\n", "12")),
}


def _pred_expr(pred: Tuple[str, int], var: str = "x") -> str:
    kind, k = pred
    return f"{var} % {k} == 0" if kind == "div" else f"{var} % 10 == {k}"


def _pred_words(pred: Tuple[str, int]) -> str:
    kind, k = pred
    return f"кратных {k}" if kind == "div" else f"оканчивающихся на {k}"


# ---------------------------------------------------------------------------
# Шаблоны эталонов
# ---------------------------------------------------------------------------

def etalons_A(agg: str, pred: Tuple[str, int]) -> List[str]:
    p = _pred_expr(pred)
    init = {"max": "0", "min": "30001", "sum": "0", "count": "0"}[agg]
    if agg == "max":
        upd1 = f"    if {p} and x > m:\n        m = x\n"
        upd2 = f"    if {p}:\n        m = max(m, x)\n"
        fin = f"print(max(x for x in a if {p}))\n"
    elif agg == "min":
        upd1 = f"    if {p} and x < m:\n        m = x\n"
        upd2 = f"    if {p}:\n        m = min(m, x)\n"
        fin = f"print(min(x for x in a if {p}))\n"
    elif agg == "sum":
        upd1 = f"    if {p}:\n        m += x\n"
        upd2 = f"    if {p}:\n        m = m + x\n"
        fin = f"print(sum(x for x in a if {p}))\n"
    else:
        upd1 = f"    if {p}:\n        m += 1\n"
        upd2 = f"    if {p}:\n        m = m + 1\n"
        fin = f"print(len([x for x in a if {p}]))\n"
    e1 = f"n = int(input())\nm = {init}\nfor i in range(n):\n    x = int(input())\n{upd1}print(m)\n"
    e2 = f"n = int(input())\nm = {init}\nfor i in range(n):\n    x = int(input())\n{upd2}print(m)\n"
    e3 = f"n = int(input())\na = [int(input()) for i in range(n)]\n{fin}"
    e4 = (f"n = int(input())\nm = {init}\ni = 0\nwhile i < n:\n    x = int(input())\n"
          f"{upd1}    i += 1\nprint(m)\n")
    return [e1, e2, e3, e4]


def etalons_B(end: int) -> List[str]:
    c = f"x % 6 == 0 and x % 10 == {end}"
    e1 = f"s = 0\nx = int(input())\nwhile x != 0:\n    if {c}:\n        s += x\n    x = int(input())\nprint(s)\n"
    e2 = f"s = 0\nwhile True:\n    x = int(input())\n    if x == 0:\n        break\n    if {c}:\n        s += x\nprint(s)\n"
    e3 = f"s = 0\nx = int(input())\nwhile x > 0:\n    if {c}:\n        s = s + x\n    x = int(input())\nprint(s)\n"
    return [e1, e2, e3]


def etalons_C() -> List[str]:
    e1 = ("a = []\nx = int(input())\nwhile x != 0:\n    a.append(x)\n    x = int(input())\n"
          "a.sort()\nprint(a[-1] + a[-2])\nprint(a[0] + a[1])\n")
    e2 = ("a = []\nwhile True:\n    x = int(input())\n    if x == 0:\n        break\n    a.append(x)\n"
          "a.sort()\nprint(a[-1] + a[-2])\nprint(a[0] + a[1])\n")
    e3 = ("a = []\nx = int(input())\nwhile x != 0:\n    a.append(x)\n    x = int(input())\n"
          "a = sorted(a)\nprint(a[-1] + a[-2])\nprint(a[0] + a[1])\n")
    return [e1, e2, e3]


def etalons_D(agg: str, cond: Tuple[str, int]) -> List[str]:
    op, lim = cond
    yn = "if f:\n    print('YES')\nelse:\n    print('NO')\n"
    if agg == "max":
        e1 = (f"n = int(input())\nm = 0\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    if v > m:\n        m = v\n    if v {op} {lim}:\n        f = True\nprint(m)\n{yn}")
        e2 = (f"n = int(input())\na = [int(input()) for i in range(n)]\nprint(max(a))\n"
              f"if min(a) {op} {lim}:\n    print('YES')\nelse:\n    print('NO')\n")
        e3 = (f"n = int(input())\nm = 0\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    m = max(m, v)\n    if v {op} {lim}:\n        f = True\nprint(m)\n{yn}")
    elif agg == "min":
        e1 = (f"n = int(input())\nm = 301\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    if v < m:\n        m = v\n    if v {op} {lim}:\n        f = True\nprint(m)\n{yn}")
        e2 = (f"n = int(input())\na = [int(input()) for i in range(n)]\nprint(min(a))\n"
              f"if max(a) {op} {lim}:\n    print('YES')\nelse:\n    print('NO')\n")
        e3 = (f"n = int(input())\nm = 301\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    m = min(m, v)\n    if v {op} {lim}:\n        f = True\nprint(m)\n{yn}")
    else:  # avg
        e1 = (f"n = int(input())\ns = 0\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    s += v\n    if v {op} {lim}:\n        f = True\nprint(round(s / n, 1))\n{yn}")
        e2 = (f"n = int(input())\na = [int(input()) for i in range(n)]\nprint(round(sum(a) / n, 1))\n"
              f"if max(a) {op} {lim}:\n    print('YES')\nelse:\n    print('NO')\n")
        e3 = (f"n = int(input())\ns = 0\nf = False\nfor i in range(n):\n    v = int(input())\n"
              f"    s = s + v\n    if v {op} {lim}:\n        f = True\nprint(f'{{s / n:.1f}}')\n{yn}")
    return [e1, e2, e3]


def build_etalons(task_id: int) -> List[str]:
    s = SPECS[task_id]
    if s["fam"] == "A":
        return etalons_A(s["agg"], s["pred"])
    if s["fam"] == "B":
        return etalons_B(s["end"])
    if s["fam"] == "C":
        return etalons_C()
    return etalons_D(s["agg"], s["cond"])


def describe(task_id: int) -> str:
    """Короткое описание задания для критериев."""
    s = SPECS[task_id]
    if s["fam"] == "A":
        agg = {"max": "максимальное", "min": "минимальное", "sum": "сумма", "count": "количество"}[s["agg"]]
        return f"{agg} среди чисел, {_pred_words(s['pred'])}; сначала N, затем N чисел"
    if s["fam"] == "B":
        return f"сумма чисел, кратных 6 и оканчивающихся на {s['end']}; ввод до 0"
    if s["fam"] == "C":
        return "сумма двух наибольших и сумма двух наименьших; ввод до 0"
    agg = {"max": "максимальная", "min": "минимальная", "avg": "средняя"}[s["agg"]]
    op, lim = s["cond"]
    return f"камера: {agg} скорость, затем YES, если хотя бы одна скорость {op} {lim}"
