# -*- coding: utf-8 -*-
"""
tsk-1391: шаг нормализации `decimal_comma` — «0,5» засчитывается за эталон «0.5».

Шаг заменяет запятую на точку, только если вся строка — одно число. Списки
«1,2,3» и текст не трогаются, поэтому шаг безопасен для списковых ответов.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

service = CheckingService()
STEPS = ["trim", "lower", "decimal_comma", "collapse_spaces"]


def _rules(accepted: list[str], normalization: list[str]) -> SolutionRules:
    """Правила SA с заданными эталонами и шагами."""
    return SolutionRules.model_validate(
        {
            "max_score": 1,
            "scoring_mode": "all_or_nothing",
            "short_answer": {
                "normalization": normalization,
                "accepted_answers": [{"value": v, "score": 1} for v in accepted],
            },
        }
    )


def _is_correct(value: str, accepted: list[str], steps: list[str] = STEPS) -> bool:
    """Прогоняет ответ через движок проверки и возвращает вердикт."""
    content = TaskContent.model_validate({"type": "SA", "stem": "Вопрос."})
    answer = StudentAnswer(type="SA", response=StudentResponse(value=value))
    return service.check_task(content, _rules(accepted, steps), answer).is_correct


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0,5", "0.5"),
        (" 1,164 ", "1.164"),
        ("-3,1", "-3.1"),
        ("0.5", "0.5"),
        ("1,2,3", "1,2,3"),
        ("1, 2", "1, 2"),
        ("x = 0,5", "x = 0,5"),
        ("05", "05"),
    ],
)
def test_decimal_comma_step(raw: str, expected: str) -> None:
    assert CheckingService._normalize_text(raw, ["decimal_comma"]) == expected


@pytest.mark.parametrize("value", ["0,5", "0.5", " 0,5 "])
def test_comma_answer_accepted(value: str) -> None:
    assert _is_correct(value, ["0.5"])


@pytest.mark.parametrize("value", ["05", "5", "0,6", "0 5"])
def test_wrong_answers_rejected(value: str) -> None:
    assert not _is_correct(value, ["0.5"])


def test_comma_in_etalon_works_both_ways() -> None:
    assert _is_correct("3.1", ["3,1"])


def test_step_before_strip_punctuation() -> None:
    steps = ["trim", "lower", "decimal_comma", "strip_punctuation", "collapse_spaces"]
    assert _is_correct("0,5", ["0.5"], steps)
    assert not _is_correct("05", ["0.5"], steps)


def test_list_answer_not_broken() -> None:
    """Списки через запятую не превращаются в «1.2.3»: сравнение как раньше."""
    assert _is_correct("1,2,3", ["1,2,3"])
    assert not _is_correct("1.2,3", ["1,2,3"])


def test_without_step_comma_not_accepted() -> None:
    assert not _is_correct("0,5", ["0.5"], ["trim", "lower"])


def test_schema_accepts_step() -> None:
    rules = _rules(["0.5"], STEPS)
    assert "decimal_comma" in rules.short_answer.normalization
