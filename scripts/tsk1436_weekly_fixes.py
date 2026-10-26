"""
Правки по недельному разбору трудных заданий 19–26.10.2026 (tsk-1436).

* **5640, 5644** («глухие», не сдал ни один ученик): эталон — программа целиком,
  сверка `code_ast` (класс tsk-950: верные программы с другими именами или
  f-строкой вместо запятой не совпадают). Переводим на прогон тестами
  `io_tests` (tsk-953), `short_answer` снимаем. У 5644 в условие дописываем
  точный формат вывода — иначе «выведи двумя строками» допускает и голые числа.
* **272**: условие «элемент с индексом i повторён (i+1) раз» буквально читается
  и как повтор элементов в списке — так ответили 10 учеников. Добавляем второй
  принимаемый ответ.

Запуск::

    python scripts/tsk1436_weekly_fixes.py --dry-run   # было → стало + прогон прошлых сдач
    DBCHECK_OK=1 python scripts/tsk1436_weekly_fixes.py --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.checking import StudentAnswer  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk953_fill_io_tests import _api  # noqa: E402

logger = logging.getLogger("tsk1436")
REPORT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk1436"

#: Тесты для 5640: (ведомая, ведущая) → round(ведомая / ведущая, 2).
GEARS = [(40, 20), (30, 12), (17, 6), (10, 3)]

IO_TESTS: Dict[int, Dict[str, Any]] = {
    5640: {
        "tests": [
            {"stdin": f"{a}\n{b}\n", "expected_stdout": f"{round(a / b, 2)}\n",
             "label": "пример" if i == 0 else f"{a}/{b}"}
            for i, (a, b) in enumerate(GEARS)
        ],
        "compare": "numeric",
    },
    5644: {
        "tests": [{"stdin": "", "expected_stdout": "Температура: 25\nОбороты: 1500\n",
                   "label": "строка из условия"}],
        "compare": "tokens",
    },
}

STEM_5644_OLD = "Используй имена temp и rpm."
STEM_5644_NEW = (
    "Используй имена temp и rpm. Вывод — ровно две строки: "
    "<code>Температура: 25</code> и <code>Обороты: 1500</code>."
)

EXTRA_272 = (
    "['apple', 'banana', 'banana', 'orange', 'orange', 'orange', "
    "'grape', 'grape', 'grape', 'grape']"
)


def new_payload(task_id: int, current: Dict[str, Any]) -> Dict[str, Any]:
    """Собрать тело PATCH для задания; правила проверяются схемой до отправки."""
    rules = copy.deepcopy(current.get("solution_rules") or {})
    body: Dict[str, Any] = {}
    if task_id in IO_TESTS:
        rules["io_tests"] = IO_TESTS[task_id]
        rules["short_answer"] = None
        rules["manual_review_required"] = False
        if task_id == 5644:
            content = copy.deepcopy(current["task_content"])
            if STEM_5644_OLD not in content["stem"]:
                raise RuntimeError("5644: опорная фраза условия не найдена — условие менялось")
            content["stem"] = content["stem"].replace(STEM_5644_OLD, STEM_5644_NEW, 1)
            body["task_content"] = content
    elif task_id == 272:
        acc = rules["short_answer"]["accepted_answers"]
        if not any(a["value"] == EXTRA_272 for a in acc):
            acc.append({"score": 1, "value": EXTRA_272})
    SolutionRules.model_validate(rules)
    body["solution_rules"] = rules
    return body


def replay(task_id: int, payload: Dict[str, Any], current: Dict[str, Any],
           subs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Прогнать прошлые сдачи учеников через новые правила."""
    svc = CheckingService()
    content = TaskContent.model_validate(payload.get("task_content") or current["task_content"])
    rules = SolutionRules.model_validate(payload["solution_rules"])
    out = []
    for s in subs:
        aj = copy.deepcopy(s["answer_json"])
        if rules.io_tests is not None:
            # Как на бою (attempts.py): программа берётся value → comment → вложение.
            from app.services.code_review_service import pick_program_for_io_tests
            resp = aj["response"]
            resp["value"] = pick_program_for_io_tests(resp.get("value"), resp.get("comment"),
                                                      None, attempt_id=None)
        ans = StudentAnswer.model_validate(aj)
        try:
            res = svc.check_task(content, rules, ans)
            verdict = res.is_correct
        except Exception as exc:  # отчёт о сбое, а не молчание
            logger.warning("id=%s: сдача %s не проверилась: %s", task_id, s["id"], exc)
            verdict = f"error: {exc}"
        out.append({"result_id": s["id"], "was": s["is_correct"], "now": verdict})
    return out


def main() -> None:
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
    for task_id in (5640, 5644, 272):
        current = _api("GET", f"/tasks/{task_id}")
        (REPORT_DIR / f"before_{task_id}.json").write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = new_payload(task_id, current)
        row: Dict[str, Any] = {"id": task_id,
                               "replay": replay(task_id, payload, current, subs.get(str(task_id), []))}
        if args.apply:
            updated = _api("PATCH", f"/tasks/{task_id}", payload)
            row["after_rules"] = updated.get("solution_rules")
            row["after_stem_has_format"] = STEM_5644_NEW in (updated.get("task_content") or {}).get("stem", "")
            logger.info("id=%s: записано", task_id)
        else:
            row["payload"] = payload
        report.append(row)
        logger.info("id=%s: прогон сдач %s", task_id, [(r["was"], r["now"]) for r in row["replay"]])
    name = "apply.json" if args.apply else "dry_run.json"
    (REPORT_DIR / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("отчёт: %s", REPORT_DIR / name)


if __name__ == "__main__":
    main()
