# -*- coding: utf-8 -*-
"""tsk-950: проверка эталонов исполнением и замер доли совпадений по code_ast.

Запуск из корня LMS: python <этот файл>
Печатает: (1) все ли эталоны и варианты верны на примере из условия и на
случайных тестах; (2) долю вариантов, совпавших хотя бы с одним эталоном по
`CheckingService._canon_code` (ровно то, что на проде); (3) ту же долю после
обезличивания имён переменных — насколько помог бы дешёвый апгрейд code_ast.
"""
from __future__ import annotations

import ast
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsk950_oge16_specs import SPECS, build_etalons  # noqa: E402
from tsk950_oge16_variants import VARIANTS  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "reviews" / "tsk950" / "measure_result.json"


# ---------------------------------------------------------------------------
# Образцовые функции для случайных тестов
# ---------------------------------------------------------------------------

def _ref(task_id: int) -> Callable[[List[int]], str]:
    s = SPECS[task_id]
    if s["fam"] == "A":
        kind, k = s["pred"]
        pred = (lambda x: x % k == 0) if kind == "div" else (lambda x: x % 10 == k)

        def f(nums: List[int]) -> str:
            sel = [x for x in nums if pred(x)]
            return str({"max": max, "min": min, "sum": sum, "count": len}[s["agg"]](sel))
        return f
    if s["fam"] == "B":
        d = s["end"]
        return lambda nums: str(sum(x for x in nums if x % 6 == 0 and x % 10 == d))
    if s["fam"] == "C":
        def g(nums: List[int]) -> str:
            a = sorted(nums)
            return f"{a[-1] + a[-2]}\n{a[0] + a[1]}"
        return g
    op, lim = s["cond"]
    cmp = {"<": lambda v: v < lim, ">": lambda v: v > lim, ">=": lambda v: v >= lim}[op]

    def h(nums: List[int]) -> str:
        if s["agg"] == "max":
            first = str(max(nums))
        elif s["agg"] == "min":
            first = str(min(nums))
        else:
            first = str(round(sum(nums) / len(nums), 1))
        return first + "\n" + ("YES" if any(cmp(v) for v in nums) else "NO")
    return h


def _gen_input(task_id: int, rnd: random.Random) -> Tuple[str, List[int]]:
    s = SPECS[task_id]
    if s["fam"] == "A":
        kind, k = s["pred"]
        n = rnd.randint(1, 12)
        nums = [rnd.randint(1, 300) for _ in range(n)]
        # гарантируем наличие подходящего числа (по условию оно всегда есть)
        good = k * rnd.randint(1, 50) if kind == "div" else rnd.randint(0, 29) * 10 + k
        nums[rnd.randrange(n)] = good
        return f"{n}\n" + "\n".join(map(str, nums)) + "\n", nums
    if s["fam"] == "B":
        n = rnd.randint(1, 12)
        nums = [rnd.randint(1, 300) for _ in range(n)]
        return "\n".join(map(str, nums)) + "\n0\n", nums
    if s["fam"] == "C":
        n = rnd.randint(2, 10)
        nums = [rnd.choice([-1, 1]) * rnd.randint(1, 300) for _ in range(n)]
        return "\n".join(map(str, nums)) + "\n0\n", nums
    n = rnd.randint(1, 8)
    nums = [rnd.randint(1, 300) for _ in range(n)]
    return f"{n}\n" + "\n".join(map(str, nums)) + "\n", nums


def _run(code: str, stdin: str) -> Optional[str]:
    try:
        r = subprocess.run([sys.executable, "-c", code], input=stdin, capture_output=True,
                           text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _norm_out(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return "\n".join(line.strip() for line in s.strip().splitlines())


def verify(task_id: int, code: str, rnd: random.Random, n_random: int = 6) -> Tuple[bool, str]:
    """Верно ли решение: пример из условия, доп. тесты из условия, случайные тесты."""
    spec = SPECS[task_id]
    cases = [spec["sample"]] + list(spec.get("extra", []))
    for stdin, expected in cases:
        got = _run(code, stdin)
        if _norm_out(got) != _norm_out(expected):
            return False, f"пример: ждали {expected!r}, получили {got!r}"
    ref = _ref(task_id)
    for _ in range(n_random):
        stdin, nums = _gen_input(task_id, rnd)
        got = _run(code, stdin)
        if _norm_out(got) != _norm_out(ref(nums)):
            return False, f"случайный тест {nums}: ждали {ref(nums)!r}, получили {got!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Канон: прод (как есть) и с обезличенными именами
# ---------------------------------------------------------------------------

class _Renamer(ast.NodeTransformer):
    """Переименовывает переменные по порядку первого появления (v0, v1, ...).

    Встроенные имена (print, input, int, range, ...) не трогает.
    """

    KEEP = set(dir(__builtins__)) | {"True", "False", "None"}

    def __init__(self) -> None:
        self.map: Dict[str, str] = {}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.KEEP:
            return node
        if node.id not in self.map:
            self.map[node.id] = f"v{len(self.map)}"
        node.id = self.map[node.id]
        return node


def canon_prod(code: str) -> Optional[str]:
    return CheckingService._canon_code(code)


def canon_renamed(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code.strip())
    except SyntaxError:
        return None
    tree = _Renamer().visit(tree)
    return ast.unparse(tree)


def main() -> int:
    rnd = random.Random(950)
    report: Dict[str, object] = {"etalons": {}, "measure": {}}
    bad = 0

    # 1. Эталоны для всех 30 заданий — верность исполнением
    for task_id in sorted(SPECS):
        rows = []
        for i, code in enumerate(build_etalons(task_id)):
            ok, why = verify(task_id, code, rnd)
            if not ok:
                bad += 1
            rows.append({"i": i, "ok": ok, "why": why, "canon_ok": canon_prod(code) is not None})
        report["etalons"][task_id] = rows
        flag = "OK " if all(r["ok"] for r in rows) else "BAD"
        print(f"[{flag}] {task_id}: {len(rows)} эталона — " + ", ".join(r["why"] for r in rows if not r["ok"]))

    # 2. Замер: варианты против эталонов
    total_v = 0
    hit_prod_total = 0
    hit_ren_total = 0
    for task_id, variants in VARIANTS.items():
        et = build_etalons(task_id)
        et_prod = {canon_prod(e) for e in et}
        et_ren = {canon_renamed(e) for e in et}
        rows = []
        for i, code in enumerate(variants):
            ok, why = verify(task_id, code, rnd)
            if not ok:
                bad += 1
                print(f"  !! вариант {task_id}#{i} НЕВЕРЕН: {why}")
            hp = canon_prod(code) in et_prod
            hr = canon_renamed(code) in et_ren
            rows.append({"i": i, "correct": ok, "hit_prod": hp, "hit_renamed": hr})
        n = len(rows)
        hp_n = sum(r["hit_prod"] for r in rows)
        hr_n = sum(r["hit_renamed"] for r in rows)
        total_v += n
        hit_prod_total += hp_n
        hit_ren_total += hr_n
        report["measure"][task_id] = {"n": n, "hit_prod": hp_n, "hit_renamed": hr_n, "rows": rows}
        print(f"{task_id}: вариантов {n}, совпало по code_ast {hp_n} ({hp_n / n:.0%}), "
              f"с обезличиванием имён {hr_n} ({hr_n / n:.0%})")

    print(f"\nИТОГО: {total_v} верных решений; code_ast как на проде — {hit_prod_total} "
          f"({hit_prod_total / total_v:.0%}); с обезличиванием имён — {hit_ren_total} "
          f"({hit_ren_total / total_v:.0%}); неверных программ в наборе: {bad}")
    report["total"] = {"variants": total_v, "hit_prod": hit_prod_total, "hit_renamed": hit_ren_total,
                       "bad": bad}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
