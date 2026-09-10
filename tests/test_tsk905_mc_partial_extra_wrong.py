# -*- coding: utf-8 -*-
"""
tsk-905: MC `scoring_mode: partial` — «отметить всё» больше не гарантирует
полный балл.

Раньше `is_correct` в partial-режиме вычислялся как `base_score ==
max_score`, а это достижимо, просто отметив ВСЕ верные варианты —
независимо от того, отмечены ли вдобавок неверные. Штраф
`extra_wrong_mc` стоял в общем блоке под `elif not is_correct`, поэтому
не применялся вовсе: «отметить все варианты подряд» гарантированно
покрывало все верные и давало полный балл.

Фикс: `is_correct` в partial-режиме требует ТОЧНОГО совпадения множеств
(не только полноты покрытия верных), и после применения штрафов
пересчитывается от `final_score` — иначе на заданиях с нулевым
`extra_wrong_mc` (как в онбординге, tsk-900) `final_score` совпадал бы с
`max_score`, а `is_correct` ошибочно показывал бы `False`.

`all_or_nothing`/`custom` не тронуты — тесты ниже это тоже проверяют.
"""
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from app.schemas.checking import StudentAnswer, StudentResponse  # noqa: E402
from app.schemas.solution_rules import SolutionRules  # noqa: E402
from app.schemas.task_content import TaskContent  # noqa: E402
from app.services.checking_service import CheckingService  # noqa: E402

service = CheckingService()


def _content() -> TaskContent:
    return TaskContent.model_validate({
        "type": "MC",
        "stem": "?",
        "options": [
            {"id": "A", "text": "верный 1"},
            {"id": "B", "text": "верный 2"},
            {"id": "C", "text": "неверный"},
        ],
    })


def _rules(max_score: int, extra_wrong_mc: int = 0, wrong_answer: int = 0) -> SolutionRules:
    return SolutionRules.model_validate({
        "max_score": max_score,
        "scoring_mode": "partial",
        "correct_options": ["A", "B"],
        "penalties": {
            "wrong_answer": wrong_answer,
            "extra_wrong_mc": extra_wrong_mc,
            "missing_answer": 0,
        },
    })


def _ans(ids: list[str]) -> StudentAnswer:
    return StudentAnswer(type="MC", response=StudentResponse(selected_option_ids=ids))


# ---------- Корневой случай: «отметить всё» больше не даёт полный балл ----------

def test_select_all_with_extra_wrong_mc_is_penalized():
    """С extra_wrong_mc=1: полное покрытие + 1 лишний неверный -> балл срезан."""
    rules = _rules(max_score=2, extra_wrong_mc=1)
    result = service.check_task(_content(), rules, _ans(["A", "B", "C"]))
    assert result.score == 1  # 2 (покрытие) - 1 (штраф за C)
    assert result.is_correct is False


def test_select_all_with_extra_wrong_mc_two_wrong_options():
    """Эталонная защита не должна уйти в минус — итог не ниже нуля."""
    content = TaskContent.model_validate({
        "type": "MC", "stem": "?",
        "options": [
            {"id": "A", "text": "верный"},
            {"id": "B", "text": "неверный 1"},
            {"id": "C", "text": "неверный 2"},
        ],
    })
    rules = SolutionRules.model_validate({
        "max_score": 1, "scoring_mode": "partial", "correct_options": ["A"],
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 5, "missing_answer": 0},
    })
    result = service.check_task(content, rules, StudentAnswer(
        type="MC", response=StudentResponse(selected_option_ids=["A", "B", "C"])
    ))
    assert result.score == 0  # 1 - 5*2 -> срезано до нуля, не в минус
    assert result.is_correct is False


# ---------- Задания без штрафа (как в онбординге, tsk-900) не сломаны ----------

def test_select_all_with_zero_penalty_still_gives_full_score_and_correct():
    """
    extra_wrong_mc=0 (как у всех 10 MC-заданий онбординга) — «отметить всё»
    покрывает все верные, штрафа нет (он настроен на ноль), итог — полный
    балл, и is_correct тоже True (иначе полный балл при статусе «неверно»
    выглядел бы как баг для ученика).
    """
    rules = _rules(max_score=2, extra_wrong_mc=0)
    result = service.check_task(_content(), rules, _ans(["A", "B", "C"]))
    assert result.score == 2
    assert result.is_correct is True


def test_exact_match_unaffected():
    rules = _rules(max_score=2, extra_wrong_mc=1)
    result = service.check_task(_content(), rules, _ans(["A", "B"]))
    assert result.score == 2
    assert result.is_correct is True


def test_partial_subset_unaffected():
    rules = _rules(max_score=2, extra_wrong_mc=1)
    result = service.check_task(_content(), rules, _ans(["A"]))
    assert result.score == 1
    assert result.is_correct is False


def test_only_wrong_option_unaffected():
    rules = _rules(max_score=2, extra_wrong_mc=1)
    result = service.check_task(_content(), rules, _ans(["C"]))
    assert result.score == 0
    assert result.is_correct is False


# ---------- Другие режимы не тронуты ----------

def test_all_or_nothing_unaffected_by_select_all():
    """all_or_nothing и так требует точного совпадения — фикс сюда не применяется."""
    content = _content()
    rules = SolutionRules.model_validate({
        "max_score": 1, "scoring_mode": "all_or_nothing", "correct_options": ["A", "B"],
        "penalties": {"wrong_answer": 0, "extra_wrong_mc": 1, "missing_answer": 0},
    })
    exact = service.check_task(content, rules, _ans(["A", "B"]))
    over = service.check_task(content, rules, _ans(["A", "B", "C"]))
    assert exact.is_correct is True and exact.score == 1
    assert over.is_correct is False and over.score == 0


def test_regression_from_tsk366_half_score_unchanged():
    """Регрессия из test_checking_table_tbl_com_tsk366.py — не должна измениться."""
    content = TaskContent.model_validate({
        "type": "MC", "stem": "?",
        "options": [
            {"id": "A", "text": "раз"},
            {"id": "B", "text": "два"},
            {"id": "C", "text": "три"},
        ],
    })
    rules = SolutionRules.model_validate({
        "max_score": 10, "scoring_mode": "partial", "correct_options": ["A", "B"],
    })
    half = service.check_task(content, rules, StudentAnswer(
        type="MC", response=StudentResponse(selected_option_ids=["A"])
    ))
    full = service.check_task(content, rules, StudentAnswer(
        type="MC", response=StudentResponse(selected_option_ids=["A", "B"])
    ))
    assert half.score == 5
    assert full.is_correct is True
