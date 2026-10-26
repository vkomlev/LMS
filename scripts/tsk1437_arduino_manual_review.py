"""
Задания Arduino/C++ (курсы 1405–1415) с программой-эталоном — на ручную проверку (tsk-1437).

Код на C++ песочница не исполняет, а `code_ast` на программе целиком не
засчитывает верные решения другой формы (класс tsk-950). Решение оператора
26.10.2026: обязательная ручная проверка (`manual_review_required=true`),
движок не трогаем. `short_answer` оставляем — это образец для преподавателя;
при флаге авто-сверка не выполняется (checking_service, tsk-230).

Запуск::

    python scripts/tsk1437_arduino_manual_review.py --dry-run
    DBCHECK_OK=1 python scripts/tsk1437_arduino_manual_review.py --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.solution_rules import SolutionRules  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tsk953_fill_io_tests import _api  # noqa: E402

logger = logging.getLogger("tsk1437_arduino")
REPORT_DIR = Path(__file__).resolve().parents[1] / "reviews" / "tsk1437"

#: Отбор 26.10: активные, code_ast, io_tests пуст, эталон ≥3 строк, курсы 1405–1415.
TASK_IDS: List[int] = [
    9578, 9579, 9588, 9589, 9591, 9601, 9602, 9604, 9614, 9617, 9626, 9629, 9638,
    9641, 9652, 9653, 9662, 9665, 9666, 9675, 9678, 9687, 9696, 9697, 9698,
]


def new_rules(current: Dict[str, Any]) -> Dict[str, Any]:
    """Правила с обязательной ручной проверкой; проверяются схемой до отправки."""
    rules = copy.deepcopy(current.get("solution_rules") or {})
    sa = rules.get("short_answer") or {}
    if "code_ast" not in (sa.get("normalization") or []):
        raise RuntimeError(f"id={current['id']}: code_ast не найден — задание менялось")
    rules["manual_review_required"] = True
    rules["partial_auto_check"] = False
    SolutionRules.model_validate(rules)
    return rules


def main() -> None:
    """Точка входа: dry-run или запись."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    for task_id in TASK_IDS:
        current = _api("GET", f"/tasks/{task_id}")
        if (current.get("solution_rules") or {}).get("manual_review_required"):
            logger.info("id=%s: уже на ручной проверке — пропуск", task_id)
            continue
        (REPORT_DIR / f"before_{task_id}.json").write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        rules = new_rules(current)
        row: Dict[str, Any] = {"id": task_id, "course_id": current.get("course_id")}
        if args.apply:
            updated = _api("PATCH", f"/tasks/{task_id}", {"solution_rules": rules})
            row["manual_review_required"] = updated["solution_rules"].get("manual_review_required")
            logger.info("id=%s: записано", task_id)
        report.append(row)
    name = "arduino_apply.json" if args.apply else "arduino_dry_run.json"
    (REPORT_DIR / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("готово: %s заданий, отчёт %s", len(report), REPORT_DIR / name)


if __name__ == "__main__":
    main()
