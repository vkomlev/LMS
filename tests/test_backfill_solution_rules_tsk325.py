# -*- coding: utf-8 -*-
"""tsk-325 (F1): правила переноса answer_raw → solution_rules (чистые функции).

Проверяют классификатор и очистку из scripts/backfill_solution_rules_answer_raw_tsk325.py
на реальных образцах формата answer_raw с прода (числа, группы чисел, буквенные
токены, артефакт «— », многочастные ответы, проза-мусор). БД не трогают.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import pytest

from backfill_solution_rules_answer_raw_tsk325 import (
    build_rules,
    clean_answer,
    route_to_manual,
)


# ---------- Очистка ----------

@pytest.mark.parametrize("raw, expected", [
    ("17", "17"),
    ("  17  ", "17"),
    ("11 9881", "11 9881"),
    ("— 469784 511", "469784 511"),   # em-dash артефакт снят
    ("– 15 976339247", "15 976339247"),  # en-dash тоже
    ("24 -22671", "24 -22671"),        # внутренний минус (отрицательное) сохранён
    ("ВЕРХ 1743", "ВЕРХ 1743"),
])
def test_clean_answer(raw: str, expected: str) -> None:
    assert clean_answer(raw) == expected


# ---------- Маршрутизация ----------

@pytest.mark.parametrize("raw", [
    "17", "5435", "11 9881", "8433 5", "— 469784 511",
    "АДВБГ", "ywxz", "Петя", "C38412", "267A030", "ВЕРХ 1743", "24 -22671",
])
def test_clean_answers_go_auto(raw: str) -> None:
    """Чистые/чинибельные ответы → авто (accepted_answers)."""
    assert route_to_manual(clean_answer(raw)) is False


@pytest.mark.parametrize("raw", [
    "1) 28 2) 16 18 3) 11",          # многочастный ответ Полякова
    "1) 9 2) 84 92 3) 93",
    "на первый вопрос 22262050",      # проза-мусор РешуЕГЭ
    "на первый вопрос",
    "номер дома - 171 и номер подъезда - 701 Ответ: 171 701",
])
def test_ambiguous_go_manual(raw: str) -> None:
    """Неоднозначные ответы → ручная проверка, а не «всегда неверно»."""
    assert route_to_manual(clean_answer(raw)) is True


# ---------- Сборка правила ----------

def test_build_auto_rule_shape() -> None:
    payload, route = build_rules("17", 1)
    assert route == "auto"
    assert payload["max_score"] == 1
    assert payload["auto_check"] is True
    assert payload["manual_review_required"] is False
    assert payload["short_answer"]["normalization"] == ["trim", "lower"]
    acc = payload["short_answer"]["accepted_answers"]
    assert acc == [{"value": "17", "score": 1}]


def test_build_auto_rule_strips_dash_prefix() -> None:
    payload, route = build_rules("— 469784 511", 1)
    assert route == "auto"
    assert payload["short_answer"]["accepted_answers"][0]["value"] == "469784 511"


def test_build_manual_rule_shape() -> None:
    payload, route = build_rules("1) 28 2) 16 18 3) 11", 1)
    assert route == "manual"
    assert payload["manual_review_required"] is True
    assert payload["short_answer"] is None
    assert payload["max_score"] == 1


def test_build_uses_task_max_score() -> None:
    payload, _ = build_rules("42", 5)
    assert payload["max_score"] == 5
    assert payload["short_answer"]["accepted_answers"][0]["score"] == 5


def test_build_fallback_max_score() -> None:
    payload, _ = build_rules("42", None)
    assert payload["max_score"] == 1


def test_produced_rule_validates_against_schema() -> None:
    """Собранный JSON обязан валидироваться схемой SolutionRules (как хранит прод)."""
    from app.schemas.solution_rules import SolutionRules

    for raw in ["17", "— 8631 7311", "1) 9 2) 84 92 3) 93", "АДВБГ"]:
        payload, _ = build_rules(raw, 1)
        SolutionRules.model_validate(payload)  # не бросает
