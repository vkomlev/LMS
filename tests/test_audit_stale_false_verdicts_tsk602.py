# -*- coding: utf-8 -*-
"""tsk-602: сверка старых незачётов с нынешними правилами — чистые функции и критерий.

Главное, что проверяется: критерием расхождения служит НАСТОЯЩИЙ код проверки
(`CheckingService`), а не SQL-модель нормализации. На разборе tsk-602 SQL-модель
дала 4 ложных совпадения из 10 — расхождение с Python на неразрывном пробеле
и на шагах нормализации, которых у задания нет. Тесты фиксируют оба случая,
чтобы критерий не подменили обратно. БД не трогают.
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

from audit_stale_false_verdicts_tsk602 import _expected_of  # noqa: E402

from app.services.checking_service import CheckingService  # noqa: E402


class TestExpectedOf:
    """Короткая запись нынешнего эталона — то, что видит человек в отчёте."""

    def test_short_answer_lists_all_variants(self) -> None:
        rules = {
            "short_answer": {
                "accepted_answers": [
                    {"score": 1, "value": "1204502"},
                    {"score": 1, "value": "1204 502"},
                ]
            }
        }
        assert _expected_of(rules) == "1204502 | 1204 502"

    def test_falls_back_to_correct_options(self) -> None:
        rules = {"short_answer": None, "correct_options": ["a", "c"]}
        assert _expected_of(rules) == "варианты: a, c"

    def test_no_reference_answer(self) -> None:
        assert _expected_of({}) == "—"
        assert _expected_of(None) == "—"


class TestCriterionIsRealCheckingCode:
    """Критерий расхождения — сам сервис проверки, со всеми его тонкостями."""

    def test_nbsp_separators_do_not_match_plain_number(self) -> None:
        """Неразрывный пробел остаётся разделителем: `2 102 556 498` ≠ `2102556498`.

        SQL-модель (`[[:punct:]]`) удаляла NBSP и давала ложное совпадение —
        именно так в разбор попал result 4202, где вердикт был верен.
        """
        steps = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
        answer = "2 102 556 498"
        assert not CheckingService._matches_short_answer(answer, "2102556498", steps)

    def test_step_absent_means_punctuation_is_significant(self) -> None:
        """Без шага strip_punctuation двоеточие значимо — задание его и требует."""
        steps = ["trim", "collapse_spaces"]
        assert not CheckingService._matches_short_answer(
            "def privet()", "def privet():", steps
        )
        assert CheckingService._matches_short_answer(
            "def privet():", "def privet():", steps
        )

    def test_space_after_comma_needs_its_own_variant(self) -> None:
        """Ловушка курса 157: `10, 0` зачитывается только отдельным вариантом эталона."""
        steps = ["trim", "lower"]
        assert not CheckingService._matches_short_answer("10, 0", "10,0", steps)
        assert CheckingService._matches_short_answer("10, 0", "10, 0", steps)
