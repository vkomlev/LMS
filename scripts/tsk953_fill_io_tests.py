# -*- coding: utf-8 -*-
"""tsk-953: тесты ввода/вывода для 30 заданий ОГЭ-16 (курс 1181) и прогон корпуса.

Собирает для каждого задания блок `solution_rules.io_tests` из спецификаций
tsk-950 (`tsk950_oge16_specs.py`): пример из условия, дополнительные примеры
из условия (7244), N=1, «все числа подходят», «подходит только гарантированное»
и случайные тесты с закреплённым сидом — всего не меньше 6 (решение оператора
2026-09-15). Ожидаемый вывод считается образцовой функцией из
`tsk950_codeast_measure._ref` и сверяется исполнением ВСЕХ эталонов задания
в той же песочнице, что работает на проде (`CheckingService`).

Режимы:
    python scripts/tsk953_fill_io_tests.py                 # план: тесты по заданиям, эталоны прогнаны
    python scripts/tsk953_fill_io_tests.py --corpus        # шаг 5 задачи: 61 верное + неверные решения
    LMS_API_KEY=... python scripts/tsk953_fill_io_tests.py --dry-run   # «было → стало» по проду, без записи
    LMS_API_KEY=... python scripts/tsk953_fill_io_tests.py --apply     # PATCH /tasks/{id} (после /db-check)

Правило задания при записи: текущее правило + `io_tests` +
`manual_review_required=false` (вердикт тестов окончателен, решение оператора);
критерии, штрафы и остальное не трогаются. `content_provenance` у всех 30
заданий уже `manual_web` (tsk-947/950) — PATCH лишь обновит отметку времени.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsk950_codeast_measure import _gen_input, _ref  # noqa: E402
from tsk950_oge16_specs import SPECS, build_etalons  # noqa: E402
from tsk950_oge16_variants import VARIANTS  # noqa: E402

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

logger = logging.getLogger("tsk953")

API_BASE = os.environ.get("LMS_API_BASE", "https://api.learn.victor-komlev.ru/api/v1")
REPORT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk953"
MIN_TESTS = 6

#: Задание 7245 — средняя скорость с одним знаком после запятой: числовое
#: сравнение с допуском 0,05 и запятой как разделителем (решение оператора).
NUMERIC_TASKS = {7245: 0.05}


# ---------------------------------------------------------------------------
# Сборка тестов
# ---------------------------------------------------------------------------

def _pred(task_id: int) -> Callable[[int], bool]:
    kind, k = SPECS[task_id]["pred"]
    return (lambda x: x % k == 0) if kind == "div" else (lambda x: x % 10 == k)


def _good(task_id: int, rnd: random.Random) -> int:
    kind, k = SPECS[task_id]["pred"]
    return k * rnd.randint(1, 50) if kind == "div" else rnd.randint(0, 29) * 10 + k


def _bad(task_id: int, rnd: random.Random, *, lo: int = 1, hi: int = 300) -> int:
    pred = _pred(task_id)
    while True:
        x = rnd.randint(lo, hi)
        if not pred(x):
            return x


def _fmt_n(nums: List[int]) -> str:
    return f"{len(nums)}\n" + "\n".join(map(str, nums)) + "\n"


def _fmt_zero(nums: List[int]) -> str:
    return "\n".join(map(str, nums)) + "\n0\n"


def _boundary_inputs(task_id: int, rnd: random.Random) -> List[Tuple[str, str, List[int]]]:
    """Граничные тесты семейства: (метка, stdin, числа)."""
    s = SPECS[task_id]
    fam = s["fam"]
    if fam == "A":
        one = _good(task_id, rnd)
        all_match = [_good(task_id, rnd) for _ in range(5)]
        # подходит только одно число, остальные больше/меньше него — ловит
        # «агрегат среди всех чисел» и перепутанный предикат
        only = [_bad(task_id, rnd, lo=150, hi=300) for _ in range(5)] + [_good(task_id, rnd)]
        rnd.shuffle(only)
        return [
            ("N=1", _fmt_n([one]), [one]),
            ("все числа подходят", _fmt_n(all_match), all_match),
            ("подходит только одно", _fmt_n(only), only),
        ]
    if fam == "B":
        d = s["end"]
        good = [x for x in range(1, 301) if x % 6 == 0 and x % 10 == d]
        one = rnd.choice(good)
        all_match = rnd.sample(good, 4)
        # кратные 6 без нужной цифры и с нужной цифрой без кратности 6 — ловит
        # половину условия
        half = [x for x in range(1, 301) if (x % 6 == 0) != (x % 10 == d)]
        only = rnd.sample(half, 5) + [rnd.choice(good)]
        rnd.shuffle(only)
        return [
            ("одно число до 0", _fmt_zero([one]), [one]),
            ("все числа подходят", _fmt_zero(all_match), all_match),
            ("подходит только одно", _fmt_zero(only), only),
        ]
    if fam == "C":
        two = [rnd.randint(1, 300), rnd.randint(1, 300)]
        neg = [-5, 12, -30, 7, 25]
        same = [10, 10, 10, 10]
        return [
            ("два числа", _fmt_zero(two), two),
            ("есть отрицательные", _fmt_zero(neg), neg),
            ("все одинаковые", _fmt_zero(same), same),
        ]
    op, lim = s["cond"]
    yes = {"<": lim - 5, ">": lim + 5, ">=": lim}[op]
    no = {"<": lim + 5, ">": lim - 5, ">=": lim - 1}[op]
    # ровно граничное значение и одно «мимо»: отличает < от <=, > от >=, >= от >
    edge = [lim, lim + 1 if op == "<" else lim - 1]
    return [
        ("N=1, условие выполнено", _fmt_n([yes]), [yes]),
        ("N=1, условие не выполнено", _fmt_n([no]), [no]),
        ("ровно на границе", _fmt_n(edge), edge),
    ]


def build_io_tests(task_id: int) -> Dict[str, Any]:
    """Блок `io_tests` задания: не меньше MIN_TESTS тестов, вывод — от образцовой функции."""
    s = SPECS[task_id]
    ref = _ref(task_id)
    rnd = random.Random(953_000 + task_id)
    tests: List[Dict[str, str]] = []

    stdin, expected = s["sample"]
    tests.append({"stdin": stdin, "expected_stdout": expected.rstrip("\n") + "\n", "label": "пример из условия"})
    for stdin, expected in s.get("extra", []):
        tests.append({"stdin": stdin, "expected_stdout": expected.rstrip("\n") + "\n", "label": "пример из условия"})
    for label, stdin, nums in _boundary_inputs(task_id, rnd):
        tests.append({"stdin": stdin, "expected_stdout": ref(nums) + "\n", "label": label})
    while len(tests) < MIN_TESTS:
        stdin, nums = _gen_input(task_id, rnd)
        tests.append({"stdin": stdin, "expected_stdout": ref(nums) + "\n", "label": "случайный"})

    block: Dict[str, Any] = {"tests": tests, "compare": "lines", "timeout_sec": 2.0}
    if task_id in NUMERIC_TASKS:
        block["compare"] = "numeric"
        block["float_tolerance"] = NUMERIC_TASKS[task_id]
    return block


def rules_with_tests(task_id: int, current: Dict[str, Any] | None = None) -> SolutionRules:
    base = dict(current or {"max_score": 1})
    base["io_tests"] = build_io_tests(task_id)
    base["manual_review_required"] = False
    return SolutionRules.model_validate(base)


# ---------------------------------------------------------------------------
# Прогон через ту же проверку, что на проде
# ---------------------------------------------------------------------------

_SERVICE = CheckingService()
_CONTENT = TaskContent(type="SA_COM", stem="tsk-953")


def check(code: str, rules: SolutionRules) -> Tuple[bool, str]:
    result = _SERVICE.check_task(
        _CONTENT, rules, StudentAnswer(type="SA_COM", response=StudentResponse(value=code))
    )
    return bool(result.is_correct), (result.feedback.general if result.feedback else "")


def verify_etalons(rules_by_task: Dict[int, SolutionRules]) -> bool:
    ok = True
    for task_id, rules in rules_by_task.items():
        for i, code in enumerate(build_etalons(task_id), start=1):
            passed, msg = check(code, rules)
            if not passed:
                ok = False
                logger.error("id=%s эталон %s НЕ прошёл свои тесты: %s", task_id, i, msg)
    return ok


def wrong_solutions(task_id: int) -> List[Tuple[str, str]]:
    """Заведомо неверные программы «как ошибаются ученики» — ни одна не должна пройти."""
    s = SPECS[task_id]
    fam = s["fam"]
    out: List[Tuple[str, str]] = []
    if fam == "A":
        kind, k = s["pred"]
        agg = s["agg"]
        init = {"max": "0", "min": "30001", "sum": "0", "count": "0"}[agg]
        upd = {
            "max": "if {p}x > m:\n        m = x", "min": "if {p}x < m:\n        m = x",
            "sum": "if {p}True:\n        m += x", "count": "if {p}True:\n        m += 1",
        }[agg]
        p_ok = f"x % {k} == 0 and " if kind == "div" else f"x % 10 == {k} and "
        p_wrong_k = f"x % {k + 1} == 0 and " if kind == "div" else f"x % 10 == {(k + 1) % 10} and "
        p_swapped = f"x % 10 == {k % 10} and " if kind == "div" else f"x % {k if k else 7} == 0 and "
        body_all = upd.format(p="")
        body_wrong = upd.format(p=p_wrong_k)
        body_swapped = upd.format(p=p_swapped)
        body_ok = upd.format(p=p_ok)
        head = "n = int(input())\nm = {init}\nfor i in range(n):\n    x = int(input())\n    {body}\nprint(m)\n"
        out.append(("без условия отбора", head.format(init=init, body=body_all)))
        out.append(("не тот делитель/цифра", head.format(init=init, body=body_wrong)))
        out.append(("кратность вместо цифры", head.format(init=init, body=body_swapped)))
        out.append(("чтение до 0 вместо N",
                    f"m = {init}\nx = int(input())\nwhile x != 0:\n    {body_ok}\n    x = int(input())\nprint(m)\n"))
        out.append(("читает N-1 чисел",
                    f"n = int(input())\nm = {init}\nfor i in range(n - 1):\n    x = int(input())\n    {body_ok}\nprint(m)\n"))
        other = {"max": "min", "min": "max", "sum": "count", "count": "sum"}[agg]
        out.append(("другой агрегат", "n = int(input())\na = [int(input()) for i in range(n)]\n"
                    f"print({'len' if other == 'count' else other}([x for x in a if {p_ok[:-5]}]))\n"))
    elif fam == "B":
        d = s["end"]
        out.append(("только кратные 6", f"s = 0\nx = int(input())\nwhile x != 0:\n    if x % 6 == 0:\n        s += x\n    x = int(input())\nprint(s)\n"))
        out.append(("только оканчивающиеся на цифру", f"s = 0\nx = int(input())\nwhile x != 0:\n    if x % 10 == {d}:\n        s += x\n    x = int(input())\nprint(s)\n"))
        out.append(("или вместо и", f"s = 0\nx = int(input())\nwhile x != 0:\n    if x % 6 == 0 or x % 10 == {d}:\n        s += x\n    x = int(input())\nprint(s)\n"))
        out.append(("считает количество", f"s = 0\nx = int(input())\nwhile x != 0:\n    if x % 6 == 0 and x % 10 == {d}:\n        s += 1\n    x = int(input())\nprint(s)\n"))
        out.append(("читает N чисел", f"n = int(input())\ns = 0\nfor i in range(n):\n    x = int(input())\n    if x % 6 == 0 and x % 10 == {d}:\n        s += x\nprint(s)\n"))
    elif fam == "C":
        out.append(("наибольшее и наименьшее по одному", "a = []\nx = int(input())\nwhile x != 0:\n    a.append(x)\n    x = int(input())\na.sort()\nprint(a[-1])\nprint(a[0])\n"))
        out.append(("суммы перепутаны местами", "a = []\nx = int(input())\nwhile x != 0:\n    a.append(x)\n    x = int(input())\na.sort()\nprint(a[0] + a[1])\nprint(a[-1] + a[-2])\n"))
        out.append(("ноль попал в список", "a = []\nwhile True:\n    x = int(input())\n    a.append(x)\n    if x == 0:\n        break\na.sort()\nprint(a[-1] + a[-2])\nprint(a[0] + a[1])\n"))
    else:
        op, lim = s["cond"]
        agg = s["agg"]
        first = {"max": "print(max(a))", "min": "print(min(a))", "avg": "print(round(sum(a) / n, 1))"}[agg]
        rev = {"<": ">", ">": "<", ">=": "<="}[op]
        strict = {"<": "<=", ">": ">=", ">=": ">"}[op]
        head = "n = int(input())\na = [int(input()) for i in range(n)]\n"
        yn = "    print('YES')\nelse:\n    print('NO')\n"
        out.append(("обратное условие", head + f"{first}\nif any(v {rev} {lim} for v in a):\n{yn}"))
        out.append(("YES/NO без проверки", head + f"{first}\nprint('YES')\n"))
        other = {"max": "print(min(a))", "min": "print(max(a))", "avg": "print(sum(a))"}[agg]
        out.append(("не тот агрегат", head + f"{other}\nif any(v {op} {lim} for v in a):\n{yn}"))
        out.append(("строгость границы", head + f"{first}\nif any(v {strict} {lim} for v in a):\n{yn}"))
    return out


def run_corpus(rules_by_task: Dict[int, SolutionRules]) -> int:
    """Шаг 5 задачи: ≥95 % зачётов на верных решениях, 0 ложных зачётов на неверных."""
    correct_total = correct_pass = 0
    for task_id, codes in VARIANTS.items():
        for i, code in enumerate(codes, start=1):
            passed, msg = check(code, rules_by_task[task_id])
            correct_total += 1
            correct_pass += int(passed)
            if not passed:
                logger.warning("id=%s верное решение %s НЕ зачтено: %s", task_id, i, msg)
    wrong_total = wrong_pass = 0
    for task_id, rules in rules_by_task.items():
        for label, code in wrong_solutions(task_id):
            passed, _ = check(code, rules)
            wrong_total += 1
            wrong_pass += int(passed)
            if passed:
                logger.error("id=%s НЕВЕРНОЕ решение «%s» зачтено — ложный зачёт", task_id, label)
    share = 100.0 * correct_pass / correct_total if correct_total else 0.0
    logger.info("верные: зачтено %s из %s (%.1f %%); неверные: ложных зачётов %s из %s",
                correct_pass, correct_total, share, wrong_pass, wrong_total)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "corpus_result.json").write_text(json.dumps({
        "correct_total": correct_total, "correct_pass": correct_pass, "share_pct": round(share, 1),
        "wrong_total": wrong_total, "wrong_pass": wrong_pass,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if share >= 95.0 and wrong_pass == 0 else 1


# ---------------------------------------------------------------------------
# Прод через API
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: Any | None = None) -> Any:
    import urllib.error
    import urllib.request

    key = os.environ.get("LMS_API_KEY")
    if not key:
        raise RuntimeError("LMS_API_KEY не задан")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method=method,
        headers={"X-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} → {e.code}: {detail[:2000]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt == 3:
                raise
            logger.warning("%s %s: сеть (%s), повтор %s/3", method, path, e, attempt + 1)
            time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def run_lms(*, apply: bool) -> None:
    rows: List[Dict[str, Any]] = []
    report = REPORT_DIR / ("apply_result.json" if apply else "dry_run.json")
    for task_id in SPECS:
        current = _api("GET", f"/tasks/{task_id}")
        cur_rules = dict(current.get("solution_rules") or {})
        if cur_rules.get("io_tests"):
            logger.info("id=%s: io_tests уже заведён (%s тестов), пропуск",
                        task_id, len(cur_rules["io_tests"].get("tests") or []))
            continue
        new_rules = rules_with_tests(task_id, cur_rules).model_dump()
        row: Dict[str, Any] = {
            "id": task_id, "external_uid": current.get("external_uid"),
            "mrr_before": cur_rules.get("manual_review_required"),
            "criteria_status": (cur_rules.get("grading_criteria") or {}).get("status"),
            "tests": [(t["label"], t["stdin"].replace("\n", " ").strip(), t["expected_stdout"].strip())
                      for t in new_rules["io_tests"]["tests"]],
            "compare": new_rules["io_tests"]["compare"],
        }
        if apply:
            updated = _api("PATCH", f"/tasks/{task_id}", {"solution_rules": new_rules})
            up_rules = updated.get("solution_rules") or {}
            row["result"] = {
                "io_tests": len((up_rules.get("io_tests") or {}).get("tests") or []),
                "mrr": up_rules.get("manual_review_required"),
                "criteria_status": (up_rules.get("grading_criteria") or {}).get("status"),
                "provenance": (updated.get("content_provenance") or {}).get("source"),
            }
            logger.info("id=%s: записано %s", task_id, row["result"])
        else:
            logger.info("id=%s: %s тестов (%s), mrr %s → False, критерии %s", task_id,
                        len(row["tests"]), row["compare"], row["mrr_before"], row["criteria_status"])
        rows.append(row)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("отчёт: %s (%s строк)", report, len(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", action="store_true", help="прогнать корпус верных и неверных решений")
    parser.add_argument("--dry-run", action="store_true", help="показать «было → стало» по проду")
    parser.add_argument("--apply", action="store_true", help="записать io_tests через PATCH /tasks/{id}")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rules_by_task = {task_id: rules_with_tests(task_id) for task_id in SPECS}
    for task_id, rules in rules_by_task.items():
        assert rules.io_tests is not None
        logger.info("id=%s: %s тестов, compare=%s", task_id, len(rules.io_tests.tests), rules.io_tests.compare)
    if not verify_etalons(rules_by_task):
        logger.error("эталоны не проходят собственные тесты — стоп")
        return 2
    logger.info("все %s эталонов прошли свои тесты", sum(len(build_etalons(t)) for t in SPECS))

    if args.corpus:
        # Только локальная песочница, к проду не обращается (`--dry-run` рядом
        # допустим — это подсказка хуку db_write_gate, что записи нет).
        return run_corpus(rules_by_task)
    if args.dry_run or args.apply:
        run_lms(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
