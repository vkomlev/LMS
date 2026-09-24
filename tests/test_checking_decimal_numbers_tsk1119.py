# -*- coding: utf-8 -*-
"""
tsk-1119: strip_punctuation склеивал цифры вокруг знака внутри числа, и «22»
засчитывалось за эталон «2.2». Теперь при strip_punctuation, если обе стороны —
одно число и хотя бы у одной есть дробная часть, вердикт выносится по значению.
"""
import pytest

from app.services.checking_service import CheckingService

STEPS = ["trim", "lower", "strip_punctuation", "collapse_spaces"]


@pytest.mark.parametrize(
    "answer, etalon, expected",
    [
        ("2.2", "2.2", True),
        ("2,2", "2.2", True),
        ("2.20", "2,2", True),
        ("22", "2.2", False),  # главный ложный зачёт
        ("2.2", "22", False),
        ("5", "5.0", True),
        ("5.0", "5", True),
        ("50", "5.0", False),
        ("42500", "4250.0", False),
        ("0.4", "0.4", True),
        ("4", "0.4", False),
        ("-1.5", "-1,5", True),
        ("90 900", "90900", True),  # разрядный пробел — прежнее послабление
        ("0101", "101", False),  # целые без дробной части — прежняя логика
        ("101", "101", True),
    ],
)
def test_decimal_numbers(answer: str, etalon: str, expected: bool) -> None:
    """Числа с дробной частью сравниваются по значению."""
    assert CheckingService._matches_short_answer(answer, etalon, STEPS) is expected


def test_without_strip_punctuation_unchanged() -> None:
    """Без strip_punctuation поведение прежнее: «5» не равно «5.0»."""
    assert CheckingService._matches_short_answer("5", "5.0", ["trim"]) is False


@pytest.mark.parametrize(
    "answer, etalon, expected",
    [
        ("кто-то", "кто-то", True),
        ("ответ: 2.2", "ответ 2.2", True),
        ("25/12/2024", "25/12/2024", True),
    ],
)
def test_text_answers_unchanged(answer: str, etalon: str, expected: bool) -> None:
    """Текст, даты и смешанные ответы идут прежней логикой."""
    assert CheckingService._matches_short_answer(answer, etalon, STEPS) is expected
