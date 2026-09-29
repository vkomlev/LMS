# -*- coding: utf-8 -*-
"""
tsk-1136: в ответе из нескольких чисел strip_punctuation склеивал «1.0» в «10»,
и «C: 10» засчитывалось за эталон «C: 1.0». Теперь при совпавших скелетах
дробные числа судятся по значению окончательно.
"""
import pytest

from app.services.checking_service import CheckingService

STEPS = ["trim", "lower", "strip_punctuation", "collapse_spaces"]
ETALON_351 = "A: 2.33\nB: 1.33\nC: 1.0"


@pytest.mark.parametrize(
    "answer, etalon, expected",
    [
        ("A: 2.33\nB: 1.33\nC: 10", ETALON_351, False),  # ложный зачёт до tsk-1136
        ("A: 233\nB: 133\nC: 10", ETALON_351, False),
        ("A: 2.33\nB: 1.33\nC: 1.00", ETALON_351, True),  # tsk-1131
        ("A: 2.33\nB: 1.33\nC: 1.0", ETALON_351, True),
        ("a: 2,5 b: 1", "A: 2.5 B: 1", True),  # запятая = точка
        ("x 25 y 1", "x 2.5 y 1", False),
        ("x 2.5 y 1", "x 25 y 1", False),
        ("ip 192.168.1.10", "ip 192.168.1.10", True),  # цепочки — прежняя логика
    ],
)
def test_numbers_by_value_is_final(answer: str, etalon: str, expected: bool) -> None:
    """Дробь в многотокенном ответе не склеивается в целое."""
    assert CheckingService._matches_short_answer(answer, etalon, STEPS) is expected


def test_code_ast_program_equal_wins() -> None:
    """code_ast: одинаковая программа засчитывается, хоть числа записаны по-разному."""
    steps = ["trim", "code_ast", "strip_punctuation", "collapse_spaces"]
    assert CheckingService._matches_short_answer("x = 0.5", "x = .5", steps) is True
    assert CheckingService._matches_short_answer("x = 10", "x = 1.0", steps) is False
