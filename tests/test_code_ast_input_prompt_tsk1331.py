# -*- coding: utf-8 -*-
"""
tsk-1331: code_ast не сравнивает подсказку в input(...).

Подсказка ввода не влияет на решение: сдача 44628 по заданию 5641 верна,
но отвергалась из-за «Введите температуру:» против «Температура: ».
Остальные строковые литералы (текст в print) по-прежнему значимы.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.checking_service import CheckingService

_matches = CheckingService._matches_short_answer

# Нормализация задания 5641 на проде.
STEPS = ["trim", "strip_punctuation", "collapse_spaces", "code_ast"]

ACCEPTED_5641 = (
    'temperatura = int(input("Температура: "))\n'
    "if temperatura > 90:\n"
    '    print("АВАРИЯ")\n'
    "elif temperatura > 70:\n"
    '    print("ВНИМАНИЕ")\n'
    "else:\n"
    '    print("НОРМА")'
)

# Сдача 44628 (ученик 4569) — дословно.
ANSWER_44628 = (
    'temperatura = int(input("Введите температуру:"))\n'
    "if temperatura > 90:\n"
    ' print("АВАРИЯ")\n'
    "elif temperatura > 70:\n"
    ' print("ВНИМАНИЕ")\n'
    "else:\n"
    ' print("НОРМА")'
)


def test_submission_44628_accepted() -> None:
    """Другая подсказка в input() — зачёт."""
    assert _matches(ANSWER_44628, ACCEPTED_5641, STEPS) is True


def test_input_without_prompt_accepted() -> None:
    """input() без подсказки — та же программа."""
    answer = ANSWER_44628.replace('input("Введите температуру:")', "input()")
    assert _matches(answer, ACCEPTED_5641, STEPS) is True


def test_other_print_text_rejected() -> None:
    """Другой текст в print — по-прежнему незачёт."""
    answer = ANSWER_44628.replace('"АВАРИЯ"', '"ТРЕВОГА"')
    assert _matches(answer, ACCEPTED_5641, STEPS) is False


def test_other_threshold_rejected() -> None:
    """Логика решения по-прежнему значима."""
    answer = ANSWER_44628.replace("> 90", "> 80")
    assert _matches(answer, ACCEPTED_5641, STEPS) is False
