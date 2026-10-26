"""
Перевод кодовых заданий Python (курсы 862–869) с code_ast на io_tests (tsk-1437).

Эталон у этих заданий — программа целиком, а сверка `code_ast` (класс tsk-950)
не засчитывает верные программы с другими именами или формой. Однострочные
фрагменты («напиши команду print…») остаются на code_ast — там он годится.
Переводим на прогон тестами `io_tests` (tsk-953), `short_answer` снимаем.
У 5646 в условие дописываем точный формат строк — иначе «значение и слово
АВАРИЯ» допускает и «90 АВАРИЯ», и «АВАРИЯ 90».

Контрольные неверные программы прогоняются вместе с прошлыми сдачами: каждая
обязана НЕ пройти, иначе тесты слабые и запись не делается.

Запуск::

    python scripts/tsk1437_code_ast_to_io_tests.py --dry-run --subs subs.json
    DBCHECK_OK=1 python scripts/tsk1437_code_ast_to_io_tests.py --apply --subs subs.json
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk953_fill_io_tests import _api  # noqa: E402

logger = logging.getLogger("tsk1437")
REPORT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk1437"


def _t(stdin: str, out: str, label: str) -> Dict[str, str]:
    """Один тест ввода/вывода."""
    return {"stdin": stdin, "expected_stdout": out, "label": label}


def _level(t: int) -> str:
    """Уровень по порогам 5641."""
    return "АВАРИЯ" if t > 90 else "ВНИМАНИЕ" if t > 70 else "НОРМА"


def _avg(xs: List[int]) -> str:
    """Среднее, как его печатает round(..., 2)."""
    return str(round(sum(xs) / len(xs), 2))


MEASURES: List[List[int]] = [[10, 20, 35], [7], [1, 2], [100, 0, 0, 1]]

IO_TESTS: Dict[int, Dict[str, Any]] = {
    # Программа без ввода: единственный тест — вывод из условия.
    5638: {"tests": [_t("", "Привод М1\nТемпература: 25\n", "вывод из условия")],
           "compare": "lines"},
    # Пороги строгие: 90 и 70 — граничные значения.
    5641: {"tests": [_t(f"{v}\n", f"{_level(v)}\n", f"t={v}")
                     for v in (95, 91, 90, 80, 71, 70, 25, -5)],
           "compare": "lines"},
    5642: {"tests": [_t(f"{len(xs)}\n" + "".join(f"{x}\n" for x in xs),
                        f"{sum(xs)}\n{_avg(xs)}\n", f"n={len(xs)}")
                     for xs in MEASURES],
           "compare": "numeric"},
    5643: {"tests": [_t("", "56.2\n95\n2\n", "список из условия")], "compare": "numeric"},
    5645: {"tests": [_t("", "31.0\n41\n", "список из условия")], "compare": "numeric"},
    5646: {"tests": [_t("", "20: норма\n35: норма\n90: АВАРИЯ\n41: норма\n95: АВАРИЯ\n"
                            "56.2\n95\n2\n", "список из условия")],
           "compare": "lines"},
}

STEM_5646_OLD = "иначе выведи значение и слово норма."
STEM_5646_NEW = (
    "иначе выведи значение и слово норма. Формат строк — как в комментарии: "
    "<code>90: АВАРИЯ</code>, <code>20: норма</code>."
)

#: Неверные программы: каждая обязана не пройти новые тесты.
CONTROLS: Dict[int, List[Tuple[str, str]]] = {
    5638: [("без f-строки значения", 'print("Привод М1")\nprint("Температура")')],
    5641: [("нестрогий порог", 't = int(input())\nif t >= 90:\n    print("АВАРИЯ")\n'
                               'elif t >= 70:\n    print("ВНИМАНИЕ")\nelse:\n    print("НОРМА")'),
           ("жёсткий вывод", 'input()\nprint("АВАРИЯ")')],
    5642: [("делит на константу", 'n = int(input())\ns = 0\nfor i in range(n):\n    s += int(input())\n'
                                  'print(s)\nprint(round(s / 3, 2))'),
           ("подогнан под пример", 'print(65)\nprint(21.67)')],
    5643: [("нестрогое превышение", 'a = [20, 35, 90, 41, 95]\nprint(round(sum(a)/len(a), 2))\n'
                                    'print(max(a))\nprint(len([x for x in a if x >= 20]))')],
    5645: [("без округления среднего верно, но максимум минимум",
            'a = [20, 35, 28, 41]\nprint(sum(a)/len(a))\nprint(min(a))')],
    5646: [("ошибка в отступе: счётчик вне if", 'a = [20, 35, 90, 41, 95]\nk = 0\nfor x in a:\n'
            '    if x > 80:\n        print(f"{x}: АВАРИЯ")\n    k += 1\n'
            'print(round(sum(a)/len(a), 2))\nprint(max(a))\nprint(k)')],
}


def new_payload(task_id: int, current: Dict[str, Any]) -> Dict[str, Any]:
    """Собрать тело PATCH; правила проверяются схемой до отправки."""
    rules = copy.deepcopy(current.get("solution_rules") or {})
    rules["io_tests"] = IO_TESTS[task_id]
    rules["short_answer"] = None
    rules["manual_review_required"] = False
    body: Dict[str, Any] = {}
    if task_id == 5646:
        content = copy.deepcopy(current["task_content"])
        if STEM_5646_NEW not in content["stem"]:
            if STEM_5646_OLD not in content["stem"]:
                raise RuntimeError("5646: опорная фраза условия не найдена — условие менялось")
            content["stem"] = content["stem"].replace(STEM_5646_OLD, STEM_5646_NEW, 1)
        body["task_content"] = content
    SolutionRules.model_validate(rules)
    body["solution_rules"] = rules
    return body


def _check(content: TaskContent, rules: SolutionRules, program: str) -> Any:
    """Прогнать одну программу; сбой вернуть строкой, а не проглотить."""
    ans = StudentAnswer.model_validate(
        {"type": "SA_COM", "response": {"value": program}})
    try:
        return CheckingService().check_task(content, rules, ans).is_correct
    except Exception as exc:  # отчёт о сбое, а не молчание
        logger.warning("сбой проверки: %s", exc)
        return f"error: {exc}"


def replay(task_id: int, payload: Dict[str, Any], current: Dict[str, Any],
           subs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Эталон, прошлые сдачи и контрольные неверные программы через новые правила."""
    from app.services.code_review_service import pick_program_for_io_tests
    content = TaskContent.model_validate(payload.get("task_content") or current["task_content"])
    rules = SolutionRules.model_validate(payload["solution_rules"])
    ref = current["solution_rules"]["short_answer"]["accepted_answers"][0]["value"]
    out: Dict[str, Any] = {"reference": _check(content, rules, ref), "subs": [], "controls": []}
    for s in subs:
        resp = (s.get("answer_json") or {}).get("response") or {}
        prog = pick_program_for_io_tests(resp.get("value"), resp.get("comment"), None, attempt_id=None)
        out["subs"].append({"result_id": s["id"], "was": s["is_correct"],
                            "now": _check(content, rules, prog or "")})
    for name, prog in CONTROLS.get(task_id, []):
        out["controls"].append({"name": name, "now": _check(content, rules, prog)})
    return out


def main() -> None:
    """Точка входа: dry-run или запись."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    parser.add_argument("--subs", type=Path, help="JSON прошлых сдач {task_id: [...]}")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    subs = json.loads(args.subs.read_text(encoding="utf-8")) if args.subs else {}
    report = []
    for task_id in IO_TESTS:
        current = _api("GET", f"/tasks/{task_id}")
        if (current.get("solution_rules") or {}).get("io_tests"):
            logger.info("id=%s: io_tests уже есть — пропуск", task_id)
            continue
        (REPORT_DIR / f"before_{task_id}.json").write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = new_payload(task_id, current)
        rep = replay(task_id, payload, current, subs.get(str(task_id), []))
        bad = rep["reference"] is not True or any(c["now"] is not False for c in rep["controls"])
        row: Dict[str, Any] = {"id": task_id, "replay": rep}
        if bad:
            logger.error("id=%s: эталон не проходит или контроль прошёл — не пишу", task_id)
            row["skipped"] = True
        elif args.apply:
            updated = _api("PATCH", f"/tasks/{task_id}", payload)
            row["after_rules"] = updated.get("solution_rules")
            logger.info("id=%s: записано", task_id)
        else:
            row["payload"] = payload
        report.append(row)
        logger.info("id=%s: эталон=%s сдачи=%s контроль=%s", task_id, rep["reference"],
                    [(r["was"], r["now"]) for r in rep["subs"]], [c["now"] for c in rep["controls"]])
    name = "apply.json" if args.apply else "dry_run.json"
    (REPORT_DIR / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("отчёт: %s", REPORT_DIR / name)


if __name__ == "__main__":
    main()
