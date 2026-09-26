# -*- coding: utf-8 -*-
"""
tsk-1131: дробные числа внутри ответа из нескольких токенов/строк сравниваются
по значению. id-351: «C: 1.00» против эталона «C: 1.0» — раньше незачёт.
"""
import pytest

from app.services.checking_service import CheckingService

STEPS = ["trim", "lower", "strip_punctuation", "collapse_spaces"]


@pytest.mark.parametrize(
    "answer, etalon, expected",
    [
        ("A: 2.33\nB: 1.33\nC: 1.00", "A: 2.33\nB: 1.33\nC: 1.0", True),  # id-351
        ("a: 2,33 b: 1.33 c: 1.0", "A: 2.33\nB: 1.33\nC: 1.0", True),
        # ученик менее точен, чем эталон — незачёт (формат «ровно 2 знака», id-10378)
        ("a: 2.33 b: 1.33 c: 1", "A: 2.33\nB: 1.33\nC: 1.0", False),
        ("Молоко      89.9    3\n25", "    Молоко     89.90    3\n25\n", False),
        # «C: 10» против «C: 1.0» засчитывается и до tsk-1131 (текстовый путь
        # склеивает 1.0→10) — путь tsk-1131 только добавляет зачёты, не отнимает.
        ("A: 2.33\nB: 1.33\nC: 1.1", "A: 2.33\nB: 1.33\nC: 1.0", False),
        ("A: 2.33\nB: 1.33\nC: 1.01", "A: 2.33\nB: 1.33\nC: 1.0", False),
        ("A: 2.33\nC: 1.00", "A: 2.33\nB: 1.33\nC: 1.0", False),  # число пропущено
        ("B: 2.33\nA: 1.00", "A: 2.33\nB: 1.0", False),  # не тот текст
        ("1.0 2.00", "2.0 1.0", False),  # порядок важен
        (  # id-258: список округлённых корней, ученик вывел по 2 знака
            "[1.00, 1.41, 1.73, 2.00, 2.24, 2.45, 2.65, 2.83, 3.00, 3.16]",
            "[1.0, 1.41, 1.73, 2.0, 2.24, 2.45, 2.65, 2.83, 3.0, 3.16]",
            True,
        ),
        ("ip 192.168.1.10", "ip 192.168.1.1", False),  # цепочка — не дробь
        ("дата 25.12.2024 и 1.50", "дата 25.12.2024 и 1.5", False),
        ("среднее 3.50 руб", "среднее 3.5 руб", True),
        ("среднее 3.50 рубля", "среднее 3.5 руб", False),
        ("0101 2.0", "101 2", False),  # целые — как текст
        ("кто то 1.50", "кто-то 1.5", False),  # защита дефиса tsk-694
        ("кто-то 1.50", "кто-то 1.5", True),
    ],
)
def test_numbers_in_text(answer: str, etalon: str, expected: bool) -> None:
    """Числа в многотокенном ответе — по значению, остальное — как текст."""
    assert CheckingService._matches_short_answer(answer, etalon, STEPS) is expected


def test_integers_only_unchanged() -> None:
    """Без дробных чисел путь не включается: прежняя текстовая логика."""
    assert CheckingService._matches_short_answer("1 2 3", "1 2 3", STEPS) is True
    assert CheckingService._matches_short_answer("1 2 4", "1 2 3", STEPS) is False


def test_without_strip_punctuation_unchanged() -> None:
    """Без strip_punctuation путь не включается."""
    assert (
        CheckingService._matches_short_answer("C: 1.00", "C: 1.0", ["trim"]) is False
    )
